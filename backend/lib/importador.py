import io
import os
import threading
import uuid
from datetime import datetime

import pandas as pd
from google.cloud import bigquery

from lib.bancos_config import CONFIG_BANCOS
from lib.bigquery_client import get_bigquery_client
from lib.cache import invalidar_tudo
from lib.mapeamento import (
    obter_mapeamento_por_banco,
    obter_banco_nome,
    CAMPOS_SEMPRE_OBRIGATORIOS,
    GRUPOS_ALTERNATIVOS_OBRIGATORIOS,
)
from lib.setup import garantir_tabela_logs

PROJECT = os.environ.get("BIGQUERY_PROJECT_ID")
DATASET = os.environ.get("BIGQUERY_DATASET", "base_bancos")
TABELA_PRINCIPAL = os.environ.get("BIGQUERY_TABLE", "base_consolidada")
TABELA_LOOKUP = os.environ.get("BIGQUERY_LOOKUP_TABLE", "cod_indicados")
TABELA_LOGS = os.environ.get("BIGQUERY_LOGS_TABLE", "import_logs")
COLUNA_ARQUIVO = "arquivo_origem"

# Mesma lista de colunas do script original (COLUNAS_PADRAO)
COLUNAS_PADRAO = [
    "data_pagamento",
    "ade",
    "convenio",
    "produto",
    "vlr_liquido",
    "vlr_bruto",
    "prazo",
    "cod_tabela",
    "tabela",
    "data_digitacao",
    "usuario",
    "cod_corretor",
    "cod_master",
    "cod_indicado",
    "banco",
    "indicado",
]
COLUNAS_DATA = ["data_digitacao", "data_pagamento"]

# ─────────────────────────────────────────────────────────────────────────
# Camada de tratamento (regras compartilhadas com lib/mapeamento.py, pra
# nunca ficarem dessincronizadas):
#   - banco, ade, data_pagamento: sempre obrigatórios (linha é rejeitada se
#     faltar qualquer um)
#   - vlr_liquido / vlr_bruto: pelo menos um dos dois precisa estar
#     preenchido (linha só é rejeitada se os DOIS estiverem vazios)
#   - cod_corretor / cod_master: mesma regra, pelo menos um dos dois
# Os demais campos são monitorados (aparecem no resumo se vierem vazios),
# mas nunca rejeitam a linha.
# ─────────────────────────────────────────────────────────────────────────
COLUNAS_OBRIGATORIAS_SIMPLES = ["banco"] + CAMPOS_SEMPRE_OBRIGATORIOS
COLUNAS_OPCIONAIS_MONITORADAS = [
    "prazo",
    "convenio",
    "produto",
    "tabela",
    "cod_tabela",
    "usuario",
    "cod_indicado",
    "vlr_liquido",
    "vlr_bruto",
    "cod_corretor",
    "cod_master",
]

# campos citados nos grupos alternativos + os sempre-obrigatórios, usados
# no diagnóstico de "coluna de origem não encontrada no arquivo"
_CAMPOS_PARA_DIAGNOSTICO = COLUNAS_OBRIGATORIAS_SIMPLES + [
    campo for grupo in GRUPOS_ALTERNATIVOS_OBRIGATORIOS for campo in grupo
]


def _esta_vazio(serie):
    """Considera vazio: None/NaN/NaT de verdade, e também strings vazias ou
    literalmente 'none'/'nan'/'nat' (o que sobra depois de virar texto)."""
    texto = serie.astype(str).str.strip().str.lower()
    return serie.isna() | texto.isin(["", "none", "nan", "nat"])


def verificar_colunas_origem_ausentes(banco_tipo, colunas_arquivo):
    """Confere, para cada coluna final relevante (obrigatória ou parte de um
    grupo alternativo obrigatório) que tem mapeamento configurado (via
    /parametros, editável), se a coluna de origem realmente existe no
    arquivo enviado. Retorna {coluna_final: coluna_origem_esperada} para o
    que estiver faltando — é o diagnóstico mais direto para o caso de
    vlr_liquido/vlr_bruto virem vazios: normalmente é porque o nome da
    coluna no arquivo não bate com o configurado."""
    mapeamento = obter_mapeamento_por_banco(banco_tipo)
    ausentes = {}
    for col_final in _CAMPOS_PARA_DIAGNOSTICO:
        col_origem = mapeamento.get(col_final)
        if col_origem and col_origem not in colunas_arquivo:
            ausentes[col_final] = col_origem
    return ausentes



# ─────────────────────────────────────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────────────────────────────────────
def ler_arquivo(file_storage):
    nome = (file_storage.filename or "").lower()
    conteudo = file_storage.read()
    buffer = io.BytesIO(conteudo)

    if nome.endswith(".csv"):
        try:
            return pd.read_csv(buffer, sep=None, engine="python", encoding="utf-8", on_bad_lines="skip")
        except Exception:
            buffer.seek(0)
            return pd.read_csv(buffer, sep=";", encoding="latin1", engine="python", on_bad_lines="skip")

    # calamine (Rust) lê .xlsx muitas vezes mais rápido e com menos memória
    # que o openpyxl — com openpyxl, relatórios grandes passavam dos 30 s do
    # gunicorn e o worker era morto (erro 500 na importação). openpyxl fica
    # só como reserva caso o calamine não esteja instalado ou recuse o arquivo.
    try:
        return pd.read_excel(buffer, engine="calamine")
    except Exception:  # noqa: BLE001
        pass
    buffer.seek(0)
    return pd.read_excel(buffer, engine="openpyxl")


# ─────────────────────────────────────────────────────────────────────────
# Lookup de cod_indicados — schema fixo, gerenciado na página /indicados
# (adicionar linha + consultar), não mais via upload de planilha inteira.
# ─────────────────────────────────────────────────────────────────────────
_lookup_cache = {"df": None}

# As configs de banco só usam duas chaves de busca possíveis: "Cod Loja" ou
# "Nome" (verificado em todo o config_bancos.json), sempre retornando
# "Usuario". Por isso a tabela de indicados tem um schema fixo com essas
# colunas, em vez de aceitar qualquer schema de planilha.
CAMPO_POR_COLUNA_CODIGO = {
    "cod loja": "cod_loja",
    "nome": "nome",
}


def get_lookup_df(forcar_reload=False):
    if _lookup_cache["df"] is not None and not forcar_reload:
        return _lookup_cache["df"]

    from lib.indicados import carregar_todos_indicados
    df = carregar_todos_indicados()

    _lookup_cache["df"] = df
    return df


def aplicar_regra_lookup(df_origem, regra, campo_retorno="usuario"):
    df_ref = get_lookup_df()
    if df_ref.empty:
        return pd.Series([None] * len(df_origem), index=df_origem.index, dtype=object)

    df_ref = df_ref.copy()

    if "coluna_banco" in regra and "valor_banco" in regra and "banco" in df_ref.columns:
        df_ref = df_ref[
            df_ref["banco"].astype(str).str.strip().str.upper() == regra["valor_banco"].strip().upper()
        ]

    chave = regra["coluna_codigo"].strip().lower()
    campo_codigo = CAMPO_POR_COLUNA_CODIGO.get(chave)
    if not campo_codigo or campo_codigo not in df_ref.columns or campo_retorno not in df_ref.columns:
        return pd.Series([None] * len(df_origem), index=df_origem.index, dtype=object)

    df_ref[campo_codigo] = df_ref[campo_codigo].astype(str).str.strip()
    mapa = dict(zip(df_ref[campo_codigo], df_ref[campo_retorno]))

    resultado = pd.Series([None] * len(df_origem), index=df_origem.index, dtype=object)
    for col in regra["colunas_busca"]:
        if col in df_origem.columns:
            valores = df_origem[col].astype(str).str.strip()
            resultado = resultado.where(resultado.notna(), valores.map(mapa))

    return resultado


def regra_valor_condicional(df_origem, regra):
    a = to_numeric_brl(df_origem[regra["coluna_troco"]])
    b = to_numeric_brl(df_origem[regra["coluna_liquido"]])
    return b.where(b > 0, a)


def regra_extrair_antes_delimitador(df_origem, regra):
    coluna_origem = regra["coluna_origem"]
    if coluna_origem not in df_origem.columns:
        return None
    return (
        df_origem[coluna_origem]
        .astype(str)
        .str.split(regra["delimitador"], n=1)
        .str[0]
        .str.strip()
    )


def tratar_data(coluna):
    """Idêntico à versão vetorizada do script original."""
    s = pd.Series(coluna)
    ja_datetime = s.apply(lambda v: isinstance(v, (pd.Timestamp, datetime)))

    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    if ja_datetime.any():
        out.loc[ja_datetime] = pd.to_datetime(s[ja_datetime]).dt.normalize()

    resto_idx = s.index[~ja_datetime]
    if len(resto_idx) == 0:
        return out

    resto = s.loc[resto_idx]
    nulos = resto.isna()
    texto = resto.astype(str).str.strip()
    texto = texto.str.split(" ", n=1).str[0].str.strip()

    com_barra = texto.str.contains("/", na=False)
    com_traco = texto.str.contains("-", na=False)
    datas = pd.Series(pd.NaT, index=texto.index, dtype="datetime64[ns]")

    if com_barra.any():
        sub = texto[com_barra]
        datas.loc[sub.index] = pd.to_datetime(sub, format="%d/%m/%Y", errors="coerce")

    if com_traco.any():
        sub = texto[com_traco]
        traco_pos4 = sub.str.find("-") == 4
        idx_aaaa = sub[traco_pos4].index
        idx_dd = sub[~traco_pos4].index
        if len(idx_aaaa) > 0:
            datas.loc[idx_aaaa] = pd.to_datetime(sub.loc[idx_aaaa], format="%Y-%m-%d", errors="coerce")
        if len(idx_dd) > 0:
            datas.loc[idx_dd] = pd.to_datetime(sub.loc[idx_dd], format="%d-%m-%Y", errors="coerce")

    faltou = datas.isna() & ~nulos
    if faltou.any():
        datas.loc[faltou] = pd.to_datetime(texto[faltou], errors="coerce", dayfirst=True)

    out.loc[resto_idx] = datas
    return out


def to_numeric_brl(serie):
    """Converte valores numéricos vindos de planilhas brasileiras (vírgula
    decimal, ponto de milhar, símbolo de moeda) para float. Também aceita
    valores já numéricos normalmente."""

    def parse(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        if isinstance(v, (int, float)):
            return v

        s = str(v).strip().replace("R$", "").replace(" ", "")
        if s == "":
            return None

        if "," in s and "." in s:
            # formato 1.234,56 -> 1234.56
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            # só vírgula -> é o separador decimal
            s = s.replace(",", ".")

        return s

    return pd.to_numeric(serie.apply(parse), errors="coerce")


def normalizar_ade(serie):
    s = serie.astype(str).str.strip()
    return s.str.replace(r"\.0+$", "", regex=True)


def montar_dataframe_padrao(df_origem, config, banco_tipo):
    df_final = pd.DataFrame(index=df_origem.index)

    mapeamento = obter_mapeamento_por_banco(banco_tipo)
    for col_final, col_origem in mapeamento.items():
        df_final[col_final] = df_origem[col_origem] if col_origem in df_origem.columns else None

    for col, valor in config.get("fixos", {}).items():
        if col not in df_final.columns or df_final[col].isna().all():
            df_final[col] = valor

    for coluna_final, regra in config.get("regras", {}).items():
        tipo = regra.get("tipo")
        if tipo == "regra_valor_condicional":
            df_final[coluna_final] = regra_valor_condicional(df_origem, regra)
        elif tipo == "extrair_antes":
            df_final[coluna_final] = regra_extrair_antes_delimitador(df_origem, regra)
        else:
            df_final[coluna_final] = aplicar_regra_lookup(df_origem, regra)

    # "indicado" é uma coluna de TRATAMENTO — nunca vem de uma coluna do
    # arquivo (não faz parte do de-para de nenhum banco). É sempre
    # calculada aqui, reaproveitando a mesma regra de lookup do
    # cod_indicado, mas devolvendo o NOME do indicado em vez do usuário.
    # Se a linha não bater com nenhum indicado cadastrado (ex: o indicado
    # ainda não existia no momento da importação), fica vazia até alguém
    # rodar o cruzamento de dados em Manutenção depois de cadastrar esse
    # indicado.
    regra_indicado = config.get("regras", {}).get("cod_indicado")
    if regra_indicado and regra_indicado.get("tipo") not in ("regra_valor_condicional", "extrair_antes"):
        df_final["indicado"] = aplicar_regra_lookup(df_origem, regra_indicado, campo_retorno="nome")
    else:
        df_final["indicado"] = None

    for col in COLUNAS_PADRAO:
        if col not in df_final.columns:
            df_final[col] = None

    for col in COLUNAS_DATA:
        df_final[col] = tratar_data(df_final[col])

    for col in ["vlr_liquido", "vlr_bruto"]:
        df_final[col] = to_numeric_brl(df_final[col])

    return df_final[COLUNAS_PADRAO]


# ─────────────────────────────────────────────────────────────────────────
# BigQuery: schema, dedup, insert, log
# ─────────────────────────────────────────────────────────────────────────
def obter_schema_tabela_principal():
    client = get_bigquery_client()
    table = client.get_table(f"{PROJECT}.{DATASET}.{TABELA_PRINCIPAL}")
    return table.schema


_coluna_indicado_garantida = False
_lock_coluna_indicado = threading.Lock()


def garantir_coluna_indicado():
    """A tabela principal (base_consolidada) já existia antes desse
    projeto — não é criada por nós. Se a coluna de tratamento 'indicado'
    ainda não existir nela, tenta adicionar via ALTER TABLE (uma vez por
    processo). Se não tiver permissão pra isso, ignora silenciosamente:
    o valor calculado simplesmente fica de fora do INSERT sem quebrar o
    resto da importação (_ajustar_para_schema já filtra colunas que não
    existem na tabela real)."""
    global _coluna_indicado_garantida
    if _coluna_indicado_garantida:
        return

    with _lock_coluna_indicado:
        if _coluna_indicado_garantida:
            return
        try:
            client = get_bigquery_client()
            table_id = f"{PROJECT}.{DATASET}.{TABELA_PRINCIPAL}"
            table = client.get_table(table_id)
            if not any(f.name == "indicado" for f in table.schema):
                table.schema = list(table.schema) + [bigquery.SchemaField("indicado", "STRING")]
                client.update_table(table, ["schema"])
        except Exception:  # noqa: BLE001
            # Se não tiver permissão de ALTER TABLE (ou qualquer outro
            # problema), segue em frente sem quebrar a importação — o
            # valor calculado só vai ficar de fora do INSERT dessa vez.
            pass
        _coluna_indicado_garantida = True


def _valor_vazio(v):
    return v is None or (isinstance(v, float) and pd.isna(v))


def _ajustar_para_schema(df, schema_fields):
    """Filtra e converte as colunas do DataFrame para bater exatamente com o
    tipo/modo de cada campo na tabela real do BigQuery. Isso evita o erro do
    pyarrow ('Error converting ... to an appropriate pyarrow datatype') que
    acontece quando, por exemplo, uma coluna chega como int64 no pandas mas
    o campo de destino é STRING (ou é um campo REPEATED/array)."""
    campos_por_nome = {f.name: f for f in schema_fields}

    colunas_comuns = [c for c in df.columns if c in campos_por_nome]
    colunas_ignoradas = [c for c in df.columns if c not in campos_por_nome]

    df_ajustado = df[colunas_comuns].copy()

    for col in colunas_comuns:
        campo = campos_por_nome[col]

        if campo.mode == "REPEATED":
            # BigQuery espera uma lista por linha; empacota o valor escalar
            df_ajustado[col] = df_ajustado[col].apply(
                lambda v: [] if _valor_vazio(v) else [v]
            )
            continue

        tipo = campo.field_type

        if tipo == "STRING":
            df_ajustado[col] = df_ajustado[col].apply(
                lambda v: None if _valor_vazio(v) else str(v)
            )
        elif tipo in ("INTEGER", "INT64"):
            df_ajustado[col] = pd.to_numeric(df_ajustado[col], errors="coerce").astype("Int64")
        elif tipo in ("FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"):
            df_ajustado[col] = pd.to_numeric(df_ajustado[col], errors="coerce")
        # DATE/DATETIME/TIMESTAMP já chegam como datetime64 (tratados em
        # tratar_data) e o BigQuery client converte automaticamente bem.

    return df_ajustado, colunas_ignoradas


def buscar_ades_existentes(banco, ades):
    """Verifica no BigQuery quais desses ADEs já existem para esse banco.
    Usa CAST para string dos dois lados, para não depender do tipo exato
    da coluna `ade` na tabela (STRING, INT64 etc)."""
    ades = [a for a in set(ades) if a and a.lower() != "none"]
    if not ades:
        return set()

    client = get_bigquery_client()
    tabela = f"`{PROJECT}.{DATASET}.{TABELA_PRINCIPAL}`"

    query = f"""
        SELECT DISTINCT CAST(ade AS STRING) AS ade
        FROM {tabela}
        WHERE UPPER(banco) = @banco
          AND CAST(ade AS STRING) IN UNNEST(@ades)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("banco", "STRING", banco.upper()),
            bigquery.ArrayQueryParameter("ades", "STRING", ades),
        ]
    )
    rows = client.query(query, job_config=job_config).result()
    return {row["ade"] for row in rows}


def inserir_dataframe(df):
    client = get_bigquery_client()
    table_id = f"{PROJECT}.{DATASET}.{TABELA_PRINCIPAL}"

    schema = obter_schema_tabela_principal()
    df_ajustado, colunas_ignoradas = _ajustar_para_schema(df, schema)

    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    job = client.load_table_from_dataframe(df_ajustado, table_id, job_config=job_config)
    job.result()

    return colunas_ignoradas


def registrar_log(banco_tipo, banco_nome, arquivo_nome, status, total_linhas,
                   linhas_inseridas, linhas_duplicadas, arquivo_original=None, mensagem_erro=None,
                   importado_por=None):
    garantir_tabela_logs()
    client = get_bigquery_client()
    table_id = f"{PROJECT}.{DATASET}.{TABELA_LOGS}"

    log_id = uuid.uuid4().hex
    linha = pd.DataFrame([{
        "log_id": log_id,
        "timestamp": datetime.utcnow(),
        "banco_tipo": banco_tipo,
        "banco_nome": banco_nome,
        "arquivo_nome": arquivo_nome,
        "arquivo_original": arquivo_original,
        "status": status,
        "total_linhas_arquivo": total_linhas,
        "linhas_inseridas": linhas_inseridas,
        "linhas_duplicadas_ignoradas": linhas_duplicadas,
        "mensagem_erro": mensagem_erro,
        "importado_por": importado_por,
        "excluido_por": None,
        "excluido_em": None,
    }])

    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND")
    job = client.load_table_from_dataframe(linha, table_id, job_config=job_config)
    job.result()

    return log_id


# ─────────────────────────────────────────────────────────────────────────
# Fluxo completo de importação
# ─────────────────────────────────────────────────────────────────────────
def processar_importacao(banco_tipo, file_storage, importado_por=None):
    config = CONFIG_BANCOS.get(banco_tipo, {})
    nome_arquivo_original = file_storage.filename
    banco_nome = obter_banco_nome(banco_tipo)

    # Nome único por importação — mesmo espírito do script original, que
    # gerava "basebicampanhas_{timestamp}.xlsx" em salvar(). Aqui usamos o
    # prefixo "prodbanco" (não o nome bruto do arquivo enviado), que fica
    # gravado em `arquivo_origem` e é usado depois para localizar/excluir
    # o lote.
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    sufixo = uuid.uuid4().hex[:6]
    nome_log = f"prodbanco_{banco_tipo}_{timestamp}_{sufixo}.xlsx"
    colunas_origem_ausentes = {}

    try:
        garantir_coluna_indicado()

        df_origem = ler_arquivo(file_storage)

        colunas_origem_ausentes = verificar_colunas_origem_ausentes(banco_tipo, df_origem.columns)

        df_final = montar_dataframe_padrao(df_origem, config, banco_tipo)

        # "banco" sempre vem do nome resolvido (registro em /parametros ou
        # config_bancos.json), não depende de mapeamento nem de "fixos" —
        # assim funciona igual para bancos originais e bancos novos criados
        # do zero pela tela de Parâmetros.
        df_final["banco"] = banco_nome.strip().upper()
        df_final["ade"] = normalizar_ade(df_final["ade"])
        df_final[COLUNA_ARQUIVO] = nome_log

        total_linhas = len(df_final)

        # ── validação: campos sempre obrigatórios ────────────────────────
        mascara_invalida = pd.Series(False, index=df_final.index)
        colunas_obrigatorias_vazias = {}
        for col in COLUNAS_OBRIGATORIAS_SIMPLES:
            vazio = _esta_vazio(df_final[col])
            if vazio.any():
                colunas_obrigatorias_vazias[col] = int(vazio.sum())
            mascara_invalida = mascara_invalida | vazio

        # ── validação: grupos "pelo menos um dos dois" ───────────────────
        # (ex: vlr_liquido/vlr_bruto, cod_corretor/cod_master) — só rejeita
        # a linha se os DOIS estiverem vazios ao mesmo tempo.
        grupos_obrigatorios_vazios = {}
        for campo_a, campo_b in GRUPOS_ALTERNATIVOS_OBRIGATORIOS:
            vazio_a = _esta_vazio(df_final[campo_a])
            vazio_b = _esta_vazio(df_final[campo_b])
            ambos_vazios = vazio_a & vazio_b
            if ambos_vazios.any():
                grupos_obrigatorios_vazios[f"{campo_a} / {campo_b}"] = int(ambos_vazios.sum())
            mascara_invalida = mascara_invalida | ambos_vazios

        linhas_rejeitadas = int(mascara_invalida.sum())
        df_final = df_final[~mascara_invalida].copy()

        # ── colunas opcionais monitoradas: não rejeita, só avisa ─────────
        # (inclui vlr_liquido/vlr_bruto/cod_corretor/cod_master individualmente,
        # mesmo que o grupo como um todo já esteja satisfeito, pra dar
        # visibilidade de qualidade de dado)
        colunas_opcionais_vazias = {}
        for col in COLUNAS_OPCIONAIS_MONITORADAS:
            vazio = _esta_vazio(df_final[col])
            if vazio.any():
                colunas_opcionais_vazias[col] = int(vazio.sum())

        total_linhas_validas = len(df_final)

        # 1) duplicados dentro do próprio arquivo enviado
        df_dedup = df_final.drop_duplicates(subset=["banco", "ade"], keep="first")
        duplicadas_arquivo = total_linhas_validas - len(df_dedup)

        # 2) duplicados já existentes no BigQuery
        ades_existentes = buscar_ades_existentes(banco_nome, df_dedup["ade"].tolist())
        df_novo = df_dedup[~df_dedup["ade"].isin(ades_existentes)]
        duplicadas_bigquery = len(df_dedup) - len(df_novo)

        duplicadas_total = duplicadas_arquivo + duplicadas_bigquery
        inseridas = 0
        colunas_ignoradas = []

        if not df_novo.empty:
            colunas_ignoradas = inserir_dataframe(df_novo)
            inseridas = len(df_novo)

        registrar_log(
            banco_tipo=banco_tipo,
            banco_nome=banco_nome,
            arquivo_nome=nome_log,
            arquivo_original=nome_arquivo_original,
            status="sucesso",
            total_linhas=total_linhas,
            linhas_inseridas=inseridas,
            linhas_duplicadas=duplicadas_total,
            importado_por=importado_por,
        )

        invalidar_tudo()

        return {
            "ok": True,
            "banco_nome": banco_nome,
            "arquivo_nome": nome_log,
            "arquivo_original": nome_arquivo_original,
            "total_linhas": total_linhas,
            "inseridas": inseridas,
            "duplicadas": duplicadas_total,
            "colunas_ignoradas": colunas_ignoradas,
            "colunas_origem_ausentes": colunas_origem_ausentes,
            "linhas_rejeitadas": linhas_rejeitadas,
            "colunas_obrigatorias_vazias": colunas_obrigatorias_vazias,
            "grupos_obrigatorios_vazios": grupos_obrigatorios_vazios,
            "colunas_opcionais_vazias": colunas_opcionais_vazias,
        }

    except Exception as exc:  # noqa: BLE001
        try:
            registrar_log(
                banco_tipo=banco_tipo,
                banco_nome=banco_nome,
                arquivo_nome=nome_log,
                arquivo_original=nome_arquivo_original,
                status="erro",
                total_linhas=0,
                linhas_inseridas=0,
                linhas_duplicadas=0,
                mensagem_erro=str(exc),
                importado_por=importado_por,
            )
        except Exception:
            pass  # não deixar falha no log mascarar o erro original

        return {
            "ok": False,
            "erro": str(exc),
            "arquivo_nome": nome_arquivo_original,
            "banco_nome": banco_nome,
            "colunas_origem_ausentes": colunas_origem_ausentes,
        }
