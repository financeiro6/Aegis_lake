"""Liga/desliga módulos inteiros do Aegis. Desligado = API do módulo não é
registrada (404); código e dados no BigQuery continuam intactos. Manter em
sincronia com frontend/src/config/funcionalidades.js.

Valores em aberto: desativado. Para reativar, definir a variável de
ambiente VALORES_ABERTOS_ATIVO=true E mudar a constante do frontend."""
import os


def _ligado(nome, padrao=False):
    valor = os.environ.get(nome)
    if valor is None:
        return padrao
    return valor.strip().lower() in ("1", "true", "sim", "yes")


VALORES_ABERTOS_ATIVO = _ligado("VALORES_ABERTOS_ATIVO")
