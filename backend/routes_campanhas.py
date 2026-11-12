import io
from datetime import datetime

from flask import Blueprint, request, jsonify, send_file

from routes_auth import login_required, requer_papel, usuario_atual
from lib.campanhas import (
    listar_campanhas,
    salvar_campanha,
    excluir_campanha,
    renovar_campanha,
    atualizar_status_campanha,
    listar_criterios,
    salvar_criterio,
    excluir_criterio,
    listar_auditoria_criterios,
    listar_campanhas_com_atingimento,
    gerar_relatorio_apuracao,
    CAMPOS_CAMPANHA,
    CAMPOS_CRITERIO,
    CAMPOS_FILTRO_PRODUCAO,
    STATUS_CAMPANHA_VALIDOS,
)
from lib.cache import invalidar_tudo

bp_campanhas = Blueprint("campanhas", __name__, url_prefix="/api")


# ─────────────────────────────────────────────────────────────────────────
# Campanhas
# ─────────────────────────────────────────────────────────────────────────
@bp_campanhas.route("/campanhas", methods=["GET"])
@requer_papel(["admin", "editor"])
def campanhas_listar():
    try:
        return jsonify(listar_campanhas())
    except Exception as exc:  # noqa: BLE001
        return jsonify({"erro": str(exc)}), 500


@bp_campanhas.route("/campanhas/atingimento")
@requer_papel(["admin", "editor", "visualizador"])
def campanhas_atingimento():
    """Campanhas + produção real do período + avaliação de faixa/meta —
    usado na Visão geral de Campanhas (cards e tabela).

    `forcar=1` limpa o cache de leitura ANTES de calcular — sem isso, o
    botão "Atualizar agora" da tela só invalidava o cache do React Query
    no navegador, forçando uma nova requisição, mas o backend tem seu
    próprio cache em memória (sem expiração por tempo) e continuava
    devolvendo o mesmo resultado de antes, então a projeção nunca era
    recalculada de fato com produção nova."""
    banco = request.args.get("banco") or None
    data_inicio = request.args.get("data_inicio") or None
    data_fim = request.args.get("data_fim") or None
    busca_campanha = request.args.get("campanha") or None
    forcar = request.args.get("forcar") in ("1", "true", "True")

    if forcar:
        invalidar_tudo()

    try:
        linhas = listar_campanhas_com_atingimento(
            banco=banco, data_inicio=data_inicio, data_fim=data_fim, busca_campanha=busca_campanha,
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"erro": str(exc)}), 500

    return jsonify(linhas)


@bp_campanhas.route("/campanhas/<id_campanha>/relatorio-apuracao/download")
@requer_papel(["admin", "editor"])
def campanhas_relatorio_apuracao_download(id_campanha):
    """Toda a produção do banco no período da campanha, com a coluna
    'Valor apuração' calculada linha a linha (aplica % especial dos
    critérios, zera o que estiver marcado "Não contabilizar")."""
    import pandas as pd

    try:
        campanha, linhas = gerar_relatorio_apuracao(id_campanha)
    except ValueError as exc:
        return jsonify({"erro": str(exc)}), 404
    except Exception as exc:  # noqa: BLE001
        return jsonify({"erro": str(exc)}), 500

    colunas_ordem = [
        "data_pagamento", "ade", "banco", "convenio", "produto", "cod_tabela", "tabela", "prazo",
        "vlr_liquido", "vlr_bruto", "usuario", "cod_corretor", "cod_master", "cod_indicado",
        "valor_apuracao",
    ]
    df = pd.DataFrame(linhas)
    for col in colunas_ordem:
        if col not in df.columns:
            df[col] = None
    df = df[colunas_ordem].rename(columns={
        "data_pagamento": "Data pagamento", "ade": "ADE (proposta)", "banco": "Banco",
        "convenio": "Convênio", "produto": "Produto", "cod_tabela": "Cód. tabela", "tabela": "Tabela",
        "prazo": "Prazo", "vlr_liquido": "Valor líquido", "vlr_bruto": "Valor bruto", "usuario": "Usuário",
        "cod_corretor": "Cód. corretor", "cod_master": "Cód. master", "cod_indicado": "Cód. indicado",
        "valor_apuracao": "Valor apuração",
    })

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Apuração")
        workbook = writer.book
        worksheet = writer.sheets["Apuração"]
        formato_moeda = workbook.add_format({"num_format": "R$ #,##0.00"})
        formato_data = workbook.add_format({"num_format": "dd/mm/yyyy"})
        worksheet.set_column("A:A", 14, formato_data)
        worksheet.set_column("B:G", 16)
        worksheet.set_column("H:H", 10)
        worksheet.set_column("I:J", 16, formato_moeda)
        worksheet.set_column("K:N", 14)
        worksheet.set_column("O:O", 16, formato_moeda)

    buffer.seek(0)
    nome_campanha = "".join(c if c.isalnum() else "_" for c in (campanha.get("campanha") or "campanha"))
    nome_arquivo = f"apuracao_{nome_campanha}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"

    return send_file(
        buffer,
        as_attachment=True,
        download_name=nome_arquivo,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp_campanhas.route("/campanhas", methods=["POST"])
@requer_papel(["admin", "editor"])
def campanhas_criar():
    dados = request.get_json(silent=True) or {}
    if not dados.get("banco") or not dados.get("campanha"):
        return jsonify({"erro": "Preencha ao menos Banco e Campanha."}), 400

    corpo = {campo: dados.get(campo) for campo in CAMPOS_CAMPANHA}
    corpo["faixas_metas"] = dados.get("faixas_metas") or []
    for campo in CAMPOS_FILTRO_PRODUCAO:
        corpo[campo] = dados.get(campo) or []
    try:
        novo_id = salvar_campanha(corpo, criado_por=usuario_atual()["email"])
    except Exception as exc:  # noqa: BLE001
        return jsonify({"erro": str(exc)}), 500
    return jsonify({"id": novo_id})


@bp_campanhas.route("/campanhas/<id_campanha>", methods=["PUT"])
@requer_papel(["admin", "editor"])
def campanhas_editar(id_campanha):
    dados = request.get_json(silent=True) or {}
    if not dados.get("banco") or not dados.get("campanha"):
        return jsonify({"erro": "Preencha ao menos Banco e Campanha."}), 400

    corpo = {campo: dados.get(campo) for campo in CAMPOS_CAMPANHA}
    corpo["faixas_metas"] = dados.get("faixas_metas") or []
    for campo in CAMPOS_FILTRO_PRODUCAO:
        corpo[campo] = dados.get(campo) or []
    try:
        salvar_campanha(corpo, criado_por=usuario_atual()["email"], id_existente=id_campanha)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"erro": str(exc)}), 500
    return jsonify({"id": id_campanha})


@bp_campanhas.route("/campanhas/<id_campanha>/status", methods=["PUT"])
@requer_papel(["admin", "editor"])
def campanhas_trocar_status(id_campanha):
    dados = request.get_json(silent=True) or {}
    novo_status = dados.get("status")
    if novo_status not in STATUS_CAMPANHA_VALIDOS:
        return jsonify({"erro": f'Status inválido. Use um de: {", ".join(STATUS_CAMPANHA_VALIDOS)}.'}), 400

    try:
        atualizar_status_campanha(id_campanha, novo_status, atualizado_por=usuario_atual()["email"])
    except ValueError as exc:
        return jsonify({"erro": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"erro": str(exc)}), 500
    return jsonify({"ok": True})


@bp_campanhas.route("/campanhas/<id_campanha>", methods=["DELETE"])
@requer_papel(["admin", "editor"])
def campanhas_excluir(id_campanha):
    try:
        excluir_campanha(id_campanha)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"erro": str(exc)}), 500
    return jsonify({"ok": True})


@bp_campanhas.route("/campanhas/<id_campanha>/renovar", methods=["POST"])
@requer_papel(["admin", "editor"])
def campanhas_renovar(id_campanha):
    """Renova a campanha: cria uma campanha nova, com o período informado,
    clonando faixas/metas e todos os critérios da campanha original."""
    dados = request.get_json(silent=True) or {}
    nova_data_inicio = dados.get("data_inicio")
    nova_data_fim = dados.get("data_fim")
    if not nova_data_inicio or not nova_data_fim:
        return jsonify({"erro": "Informe a nova data de início e de fim da apuração."}), 400

    try:
        novo_id = renovar_campanha(
            id_campanha, nova_data_inicio, nova_data_fim, criado_por=usuario_atual()["email"],
        )
    except ValueError as exc:
        return jsonify({"erro": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"erro": str(exc)}), 500
    return jsonify({"id": novo_id})


# ─────────────────────────────────────────────────────────────────────────
# Critérios
# ─────────────────────────────────────────────────────────────────────────
@bp_campanhas.route("/criterios", methods=["GET"])
@requer_papel(["admin", "editor"])
def criterios_listar():
    try:
        return jsonify(listar_criterios())
    except Exception as exc:  # noqa: BLE001
        return jsonify({"erro": str(exc)}), 500


@bp_campanhas.route("/criterios", methods=["POST"])
@requer_papel(["admin", "editor"])
def criterios_criar():
    dados = request.get_json(silent=True) or {}
    if not dados.get("banco") or not dados.get("campanha"):
        return jsonify({"erro": "Preencha ao menos Banco e Campanha."}), 400

    # A "Tabela" aceita vários códigos separados por ";" — cada código
    # vira um critério (linha) separado, com os demais campos repetidos.
    tabela_bruta = (dados.get("tabela") or "").strip()
    codigos_tabela = [c.strip() for c in tabela_bruta.split(";") if c.strip()] or [""]

    ids_criados = []
    ultimo_erro = None
    for codigo in codigos_tabela:
        corpo = {campo: dados.get(campo) for campo in CAMPOS_CRITERIO}
        corpo["tabela"] = codigo
        try:
            novo_id = salvar_criterio(corpo, criado_por=usuario_atual()["email"])
            ids_criados.append(novo_id)
        except ValueError as exc:
            ultimo_erro = str(exc)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"erro": str(exc)}), 500

    if not ids_criados:
        return jsonify({"erro": ultimo_erro or "Não foi possível salvar."}), 400

    return jsonify({"id": ids_criados[0], "ids": ids_criados, "total_criados": len(ids_criados)})


@bp_campanhas.route("/criterios/<id_criterio>", methods=["PUT"])
@requer_papel(["admin", "editor"])
def criterios_editar(id_criterio):
    dados = request.get_json(silent=True) or {}
    if not dados.get("banco") or not dados.get("campanha"):
        return jsonify({"erro": "Preencha ao menos Banco e Campanha."}), 400

    corpo = {campo: dados.get(campo) for campo in CAMPOS_CRITERIO}
    try:
        salvar_criterio(corpo, criado_por=usuario_atual()["email"], id_existente=id_criterio)
    except ValueError as exc:
        return jsonify({"erro": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"erro": str(exc)}), 500
    return jsonify({"id": id_criterio})


@bp_campanhas.route("/criterios/<id_criterio>", methods=["DELETE"])
@requer_papel(["admin", "editor"])
def criterios_excluir(id_criterio):
    try:
        excluir_criterio(id_criterio, excluido_por=usuario_atual()["email"])
    except ValueError as exc:
        return jsonify({"erro": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"erro": str(exc)}), 500
    return jsonify({"ok": True})


@bp_campanhas.route("/criterios/auditoria", methods=["GET"])
@requer_papel(["admin", "editor"])
def criterios_auditoria():
    campanha_id = request.args.get("campanha_id") or None
    try:
        return jsonify(listar_auditoria_criterios(campanha_id=campanha_id))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"erro": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────
# Importação em lote (lib/importacao_campanhas.py) — só admin
# ─────────────────────────────────────────────────────────────────────────
def _arquivo_importacao():
    arquivo = request.files.get("arquivo")
    if not arquivo or not arquivo.filename:
        raise ValueError("Anexe a planilha (.xlsx) com as abas Campanhas e Critérios.")
    if not arquivo.filename.lower().endswith(".xlsx"):
        raise ValueError("O arquivo precisa ser .xlsx.")
    return arquivo.read()


@bp_campanhas.route("/campanhas/importacao/previa", methods=["POST"])
@requer_papel(["admin"])
def campanhas_importacao_previa():
    from lib.importacao_campanhas import previa

    try:
        return jsonify(previa(_arquivo_importacao()))
    except ValueError as exc:
        return jsonify({"erro": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"erro": str(exc)}), 500


@bp_campanhas.route("/campanhas/importacao", methods=["GET", "POST"])
@requer_papel(["admin"])
def campanhas_importacao():
    from lib.importacao_campanhas import importar, listar_lotes

    try:
        if request.method == "GET":
            return jsonify(listar_lotes())
        return jsonify(importar(_arquivo_importacao(), usuario_atual()["email"]))
    except ValueError as exc:
        return jsonify({"erro": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"erro": str(exc)}), 500


@bp_campanhas.route("/campanhas/importacao/<lote>", methods=["DELETE"])
@requer_papel(["admin"])
def campanhas_importacao_desfazer(lote):
    from lib.importacao_campanhas import desfazer

    try:
        return jsonify(desfazer(lote, usuario_atual()["email"]))
    except ValueError as exc:
        return jsonify({"erro": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"erro": str(exc)}), 500
