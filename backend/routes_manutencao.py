from flask import Blueprint, jsonify

from routes_auth import login_required, requer_papel
from lib.manutencao import executar_cruzamento_indicado, listar_valores_mapeados
from lib.valores_abertos import sincronizar_valores_abertos_de_campanhas

bp_manutencao = Blueprint("manutencao", __name__, url_prefix="/api")


@bp_manutencao.route("/manutencao/cruzar-indicado", methods=["POST"])
@requer_papel(["admin"])
def manutencao_cruzar_indicado():
    try:
        resultado = executar_cruzamento_indicado()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "erro": str(exc)}), 500
    return jsonify({"ok": True, **resultado})


@bp_manutencao.route("/manutencao/sincronizar-valores-campanhas", methods=["POST"])
@requer_papel(["admin"])
def manutencao_sincronizar_valores_campanhas():
    """ETL: atualiza os lançamentos de Valores em Aberto vinculados a
    campanhas com a projeção mais recente de cada campanha (ver
    lib.valores_abertos.sincronizar_valores_abertos_de_campanhas)."""
    from lib.funcionalidades import VALORES_ABERTOS_ATIVO

    if not VALORES_ABERTOS_ATIVO:
        return jsonify({"ok": False, "erro": "O módulo Valores em aberto está desativado."}), 404
    try:
        resultado = sincronizar_valores_abertos_de_campanhas()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "erro": str(exc)}), 500
    return jsonify({"ok": True, **resultado})



@bp_manutencao.route("/manutencao/valores-mapeados")
@requer_papel(["admin", "editor", "visualizador"])
def manutencao_valores_mapeados():
    """Usado pelo Cadastro de Campanha e pela Visão geral de Produção pra
    popular filtros opcionais (Map Indicado/Convênio/Produto). Aberto a
    qualquer usuário logado com acesso a alguma dessas telas — é só uma
    lista de valores já tratados, não dado sensível."""
    try:
        return jsonify(listar_valores_mapeados())
    except Exception as exc:  # noqa: BLE001
        return jsonify({"erro": str(exc)}), 500
