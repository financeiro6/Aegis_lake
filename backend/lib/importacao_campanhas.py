"""Importação em lote de campanhas e critérios já no formato do Aegis.

Lê uma planilha com as abas "Campanhas" e "Critérios" (cabeçalho na linha
3, mesmas colunas da tela de cadastro — ex.: o arquivo "Campanhas no
modelo Aegis.xlsx") e grava tudo com UMA carga por tabela, em vez de um
salvamento por critério (milhares de alterações estourariam o limite de
alterações por tabela do BigQuery e levariam horas).

Nada muda nas regras de cálculo: cada linha vira exatamente um critério
como se tivesse sido cadastrado pela tela. Tudo o que entra numa
importação recebe o mesmo `lote_importacao`, e o lote pode ser desfeito
inteiro (remove os critérios e as campanhas criadas por ele).
"""
import hashlib
import io
import json
import math
import uuid
from datetime import date, datetime

import pandas as pd
from google.cloud import bigquery

from lib.bigquery_client import get_bigquery_client, erro_e_de_billing
from lib.cache import invalidar_tudo
from lib.campanhas import (
    BASE_PRODUCAO_VALIDOS,
    CAMPOS_FILTRO_PRODUCAO,
    DATASET,
    PROJECT,
    SCHEMA_AUDITORIA,
    SCHEMA_CAMPANHAS,
    SCHEMA_CRITERIOS,
    STATUS_CAMPANHA_VALIDOS,
    STATUS_CRITERIO_NAO_CONTABILIZAR,
    TABELA_AUDITORIA,
    TABELA_CAMPANHAS,
    TABELA_CRITERIOS,
    _garantir_tabela,
    listar_campanhas,
)

STATUS_CRITERIO_VALIDOS = ["ativo", STATUS_CRITERIO_NAO_CONTABILIZAR]

# coluna da planilha → campo do Aegis
COLUNAS_CAMPANHA = {
    "Banco": "banco", "Campanha": "campanha", "Data início": "data_inicio", "Data fim": "data_fim",
    "Status": "status", "Valor base": "base_producao",
}
COLUNAS_CRITERIO = {
    "Banco": "banco", "Campanha": "campanha", "Convênio": "convenio", "Produto": "produto",
    "Valor base": "base_producao_criterio", "Tabela": "tabela", "Descrição da tabela": "descr_tabela",
    "Prazo mínimo": "prazo_min", "Prazo máximo": "prazo_max", "Valor mínimo": "valor_min", "Valor máximo": "valor_max",
    "Data início": "data_inicio", "Data fim": "data_fim", "Status": "status", "% especial": "perc_especial",
}
TEXTO = ["Banco", "Campanha", "Convênio", "Produto", "Tabela", "Descrição da tabela", "Status", "Valor base"]
NUMERO = ["Prazo mínimo", "Prazo máximo", "Valor mínimo", "Valor máximo", "% especial"]
DATA = ["Data início", "Data fim"]
MAX_ERROS_LISTADOS = 50


def _vazio(v):
    return v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, str) and not v.strip())


def _texto(v):
    return None if _vazio(v) else str(v).strip()


def _numero(v):
    if _vazio(v):
        return None
    return float(str(v).replace(",", ".")) if isinstance(v, str) else float(v)


def _data(v):
    if _vazio(v):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return pd.to_datetime(v, dayfirst=True).date()


def _chave(banco, campanha):
    return ((banco or "").strip().upper(), (campanha or "").strip().upper())


def _ler_aba(conteudo, aba, colunas):
    try:
        df = pd.read_excel(io.BytesIO(conteudo), sheet_name=aba, header=2, dtype={c: str for c in TEXTO})
    except ValueError as exc:
        raise ValueError(f'A planilha precisa ter a aba "{aba}" (cabeçalho na linha 3).') from exc
    faltando = [c for c in colunas if c not in df.columns]
    if faltando:
        raise ValueError(f'Aba "{aba}" sem as colunas: {", ".join(faltando)}.')
    return df


def ler_planilha(conteudo):
    """Lê e valida a planilha. Devolve (campanhas, criterios, erros), com
    campos já no formato do Aegis. Linhas com erro ficam fora e entram na
    lista de erros (aba + linha da planilha + motivo)."""
    df_camp = _ler_aba(conteudo, "Campanhas", list(COLUNAS_CAMPANHA))
    df_crit = _ler_aba(conteudo, "Critérios", list(COLUNAS_CRITERIO))
    erros = []

    def converter(linha, mapa):
        item = {}
        for coluna, campo in mapa.items():
            v = linha.get(coluna)
            if coluna in NUMERO:
                item[campo] = _numero(v)
            elif coluna in DATA:
                item[campo] = _data(v)
            else:
                item[campo] = _texto(v)
        return item

    campanhas = {}
    for i, linha in df_camp.iterrows():
        n = i + 4
        try:
            c = converter(linha, COLUNAS_CAMPANHA)
        except (TypeError, ValueError) as exc:
            erros.append({"aba": "Campanhas", "linha": n, "motivo": f"valor inválido ({exc})"})
            continue
        if not c["banco"] or not c["campanha"]:
            erros.append({"aba": "Campanhas", "linha": n, "motivo": "Banco e Campanha são obrigatórios."})
            continue
        if c["status"] not in STATUS_CAMPANHA_VALIDOS:
            erros.append({"aba": "Campanhas", "linha": n, "motivo": f'Status "{c["status"]}" inválido.'})
            continue
        if c["base_producao"] not in BASE_PRODUCAO_VALIDOS:
            c["base_producao"] = "liquido"
        if c["data_inicio"] and c["data_fim"] and c["data_inicio"] > c["data_fim"]:
            erros.append({"aba": "Campanhas", "linha": n, "motivo": "Data início depois da data fim."})
            continue
        chave = _chave(c["banco"], c["campanha"])
        if chave in campanhas:
            erros.append({"aba": "Campanhas", "linha": n, "motivo": "Campanha repetida na planilha."})
            continue
        campanhas[chave] = c

    criterios = []
    for i, linha in df_crit.iterrows():
        n = i + 4
        try:
            c = converter(linha, COLUNAS_CRITERIO)
        except (TypeError, ValueError) as exc:
            erros.append({"aba": "Critérios", "linha": n, "motivo": f"valor inválido ({exc})"})
            continue
        if _chave(c["banco"], c["campanha"]) not in campanhas:
            erros.append({"aba": "Critérios", "linha": n, "motivo": "Campanha não está na aba Campanhas."})
            continue
        if c["status"] not in STATUS_CRITERIO_VALIDOS:
            erros.append({"aba": "Critérios", "linha": n, "motivo": f'Status "{c["status"]}" inválido (use ativo ou nao_contabilizar).'})
            continue
        if c["base_producao_criterio"] not in BASE_PRODUCAO_VALIDOS:
            c["base_producao_criterio"] = "liquido"
        if c["data_inicio"] and c["data_fim"] and c["data_inicio"] > c["data_fim"]:
            erros.append({"aba": "Critérios", "linha": n, "motivo": "Data início depois da data fim."})
            continue
        criterios.append(c)

    return campanhas, criterios, erros


def _assinatura(conteudo):
    return hashlib.sha256(conteudo).hexdigest()[:12]


def _lotes_existentes():
    _garantir_tabela(TABELA_CRITERIOS, SCHEMA_CRITERIOS)
    _garantir_tabela(TABELA_CAMPANHAS, SCHEMA_CAMPANHAS)
    client = get_bigquery_client()
    query = f"""
        SELECT lote_importacao AS lote, COUNT(*) AS criterios,
               MIN(criado_em) AS importado_em, ANY_VALUE(criado_por) AS importado_por
        FROM `{PROJECT}.{DATASET}.{TABELA_CRITERIOS}`
        WHERE lote_importacao IS NOT NULL
        GROUP BY lote_importacao
        ORDER BY importado_em DESC
    """
    return [dict(r) for r in client.query(query).result()]


def listar_lotes():
    return _lotes_existentes()


def previa(conteudo):
    campanhas, criterios, erros = ler_planilha(conteudo)
    existentes = {_chave(c.get("banco"), c.get("campanha")): c for c in listar_campanhas()}
    assinatura = _assinatura(conteudo)
    ja_importado = any((l.get("lote") or "").endswith(assinatura) for l in _lotes_existentes())

    por_campanha = {}
    for c in criterios:
        k = _chave(c["banco"], c["campanha"])
        por_campanha[k] = por_campanha.get(k, 0) + 1
    linhas = []
    for k, c in sorted(campanhas.items(), key=lambda kv: (kv[1]["status"] != "Vigente", kv[1]["banco"], kv[1]["campanha"])):
        linhas.append({
            "banco": c["banco"], "campanha": c["campanha"], "status": c["status"],
            "data_inicio": c["data_inicio"].isoformat() if c["data_inicio"] else None,
            "data_fim": c["data_fim"].isoformat() if c["data_fim"] else None,
            "ja_existe": k in existentes, "criterios": por_campanha.get(k, 0),
        })
    return {
        "campanhas": linhas,
        "total_campanhas": len(campanhas),
        "campanhas_novas": sum(1 for k in campanhas if k not in existentes),
        "campanhas_existentes": sum(1 for k in campanhas if k in existentes),
        "total_criterios": len(criterios),
        "criterios_ativos": sum(1 for c in criterios if c["status"] == "ativo"),
        "criterios_nao_contabilizar": sum(1 for c in criterios if c["status"] == STATUS_CRITERIO_NAO_CONTABILIZAR),
        "erros": erros[:MAX_ERROS_LISTADOS],
        "total_erros": len(erros),
        "ja_importado": ja_importado,
    }


def _carregar(client, tabela, schema, linhas):
    if not linhas:
        return
    df = pd.DataFrame(linhas)
    campos = [f for f in schema if f.name in df.columns]
    df = df[[f.name for f in campos]]
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND", schema=campos)
    client.load_table_from_dataframe(df, f"{PROJECT}.{DATASET}.{tabela}", job_config=job_config).result()


def importar(conteudo, usuario):
    """Grava campanhas novas e todos os critérios da planilha. Campanha que
    já existe (mesmo banco + nome) não é recriada: os critérios entram
    nela. Recusa o mesmo arquivo duas vezes."""
    campanhas, criterios, erros = ler_planilha(conteudo)
    if not criterios and not campanhas:
        raise ValueError("A planilha não tem campanhas nem critérios válidos.")
    assinatura = _assinatura(conteudo)
    if any((l.get("lote") or "").endswith(assinatura) for l in _lotes_existentes()):
        raise ValueError("Este arquivo já foi importado. Desfaça a importação anterior antes de importar de novo.")

    lote = f"{datetime.utcnow():%Y%m%d%H%M%S}-{assinatura}"
    agora = datetime.utcnow()
    existentes = {_chave(c.get("banco"), c.get("campanha")): c for c in listar_campanhas()}

    ids, novas = {}, []
    for k, c in campanhas.items():
        if k in existentes:
            ids[k] = existentes[k]["id"]
            continue
        ids[k] = uuid.uuid4().hex
        novas.append({
            **c, "id": ids[k], "faixas_metas": "[]",
            **{campo: "[]" for campo in CAMPOS_FILTRO_PRODUCAO},
            "criado_em": agora, "criado_por": usuario, "lote_importacao": lote,
        })

    linhas_criterio, auditoria = [], []
    for c in criterios:
        k = _chave(c["banco"], c["campanha"])
        item = {**c, "id": uuid.uuid4().hex, "campanha_id": ids[k], "banco": campanhas[k]["banco"],
                "campanha": campanhas[k]["campanha"], "criado_em": agora, "criado_por": usuario, "lote_importacao": lote}
        linhas_criterio.append(item)
        auditoria.append({"id": uuid.uuid4().hex, "criterio_id": item["id"], "campanha_id": item["campanha_id"],
                          "acao": "importado", "dados": json.dumps({**item, "lote_importacao": lote}, default=str),
                          "usuario": usuario, "timestamp": agora})

    for tabela, schema in ((TABELA_CAMPANHAS, SCHEMA_CAMPANHAS), (TABELA_CRITERIOS, SCHEMA_CRITERIOS), (TABELA_AUDITORIA, SCHEMA_AUDITORIA)):
        _garantir_tabela(tabela, schema)
    client = get_bigquery_client()
    _carregar(client, TABELA_CAMPANHAS, SCHEMA_CAMPANHAS, novas)
    _carregar(client, TABELA_CRITERIOS, SCHEMA_CRITERIOS, linhas_criterio)
    _carregar(client, TABELA_AUDITORIA, SCHEMA_AUDITORIA, auditoria)
    invalidar_tudo()

    return {"lote": lote, "campanhas_criadas": len(novas), "campanhas_existentes": len(campanhas) - len(novas),
            "criterios_criados": len(linhas_criterio), "linhas_ignoradas": len(erros)}


def _apagar_lote(client, tabela, lote):
    tabela_sql = f"`{PROJECT}.{DATASET}.{tabela}`"
    job_config = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("lote", "STRING", lote)])
    try:
        client.query(f"DELETE FROM {tabela_sql} WHERE lote_importacao = @lote", job_config=job_config).result()
    except Exception as exc:  # noqa: BLE001
        if not erro_e_de_billing(exc):
            raise
        client.query(
            f"CREATE OR REPLACE TABLE {tabela_sql} AS SELECT * FROM {tabela_sql} "
            f"WHERE lote_importacao IS NULL OR lote_importacao != @lote",
            job_config=job_config,
        ).result()


def desfazer(lote, usuario):
    """Remove os critérios e as campanhas criados por um lote. Critérios
    cadastrados pela tela (sem lote) e campanhas que já existiam antes da
    importação não são tocados. Fica um registro "importacao_desfeita" no
    histórico de critérios."""
    if not any(l.get("lote") == lote for l in _lotes_existentes()):
        raise ValueError("Importação não encontrada.")
    client = get_bigquery_client()
    _apagar_lote(client, TABELA_CRITERIOS, lote)
    _apagar_lote(client, TABELA_CAMPANHAS, lote)
    _carregar(client, TABELA_AUDITORIA, SCHEMA_AUDITORIA, [{
        "id": uuid.uuid4().hex, "criterio_id": None, "campanha_id": None, "acao": "importacao_desfeita",
        "dados": json.dumps({"lote_importacao": lote}), "usuario": usuario, "timestamp": datetime.utcnow(),
    }])
    invalidar_tudo()
    return {"lote": lote}
