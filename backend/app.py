import os
import sys
from datetime import date, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR))

from dotenv import load_dotenv

load_dotenv(BASE_DIR / ".env.local")

# Diagnóstico de deploy: lista no log quais variáveis esperadas chegaram
# (só os NOMES, nunca os valores). Nomes parecidos (espaço sobrando,
# minúsculas) aparecem como "parecidas" para achar erro de digitação.
_VARIAVEIS_ESPERADAS = [
    "BIGQUERY_PROJECT_ID", "GOOGLE_CREDENTIALS_JSON", "FLASK_SECRET_KEY",
    "ADMIN_BOOTSTRAP_EMAIL", "ADMIN_BOOTSTRAP_PASSWORD",
]
_presentes = [n for n in _VARIAVEIS_ESPERADAS if (os.environ.get(n) or "").strip()]
_faltando = [n for n in _VARIAVEIS_ESPERADAS if n not in _presentes]
_parecidas = sorted(
    repr(n) for n in os.environ
    if n not in _VARIAVEIS_ESPERADAS
    and n.strip().upper() in _VARIAVEIS_ESPERADAS
)
print(
    f"[config] variáveis presentes: {', '.join(_presentes) or 'nenhuma'} | "
    f"faltando ou vazias: {', '.join(_faltando) or 'nenhuma'}"
    + (f" | nomes parecidos (corrigir): {', '.join(_parecidas)}" if _parecidas else ""),
    flush=True,
)

from flask import Flask, jsonify, send_from_directory
from flask.json.provider import DefaultJSONProvider


class JSONProviderComData(DefaultJSONProvider):
    """Flask não sabe serializar datetime.date/datetime.datetime em JSON
    por padrão — o BigQuery devolve exatamente esses tipos. Isso converte
    automaticamente pra string ISO (ex: "2026-07-15") em qualquer resposta."""

    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return super().default(obj)


# Onde fica o build do frontend (gerado por `npm run build` dentro de
# frontend/, produzindo frontend/dist/). Em produção (Render), o Flask
# serve esses arquivos direto — front e API na mesma origem, então não
# precisa configurar CORS nem cookie cross-domain. Em desenvolvimento
# local essa pasta normalmente não existe (você roda o Vite dev server
# separado na porta 5173) — nesse caso o Flask simplesmente não serve
# nada por essas rotas, sem erro.
FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"

app = Flask(__name__)
app.json = JSONProviderComData(app)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(24)
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_HTTPONLY=True,
)

from routes_auth import bp_auth
from routes_dashboard import bp_dashboard
from routes_campanhas import bp_campanhas
from routes_usuarios import bp_usuarios
from routes_bancos import bp_bancos
from routes_importar import bp_importar
from routes_logs import bp_logs
from routes_relatorio import bp_relatorio
from routes_parametros import bp_parametros
from routes_indicados import bp_indicados
from routes_manutencao import bp_manutencao
from routes_valores_abertos import bp_valores_abertos
from routes_mapeamento_categorias import bp_mapeamento_categorias

app.register_blueprint(bp_auth)
app.register_blueprint(bp_dashboard)
app.register_blueprint(bp_campanhas)
app.register_blueprint(bp_usuarios)
app.register_blueprint(bp_bancos)
app.register_blueprint(bp_importar)
app.register_blueprint(bp_logs)
app.register_blueprint(bp_relatorio)
app.register_blueprint(bp_parametros)
app.register_blueprint(bp_indicados)
app.register_blueprint(bp_manutencao)
# Valores em aberto está desativado (lib/funcionalidades.py): sem o
# blueprint, a API do módulo responde 404.
from lib.funcionalidades import VALORES_ABERTOS_ATIVO

if VALORES_ABERTOS_ATIVO:
    app.register_blueprint(bp_valores_abertos)
app.register_blueprint(bp_mapeamento_categorias)

# Aquecimento em background (mesmo mecanismo do projeto anterior) — deixa o
# cache do SERVIDOR já quente pra qualquer usuário/sessão nova. É uma
# segunda camada, diferente do cache do React (esse aqui é compartilhado
# entre todo mundo; o do React é por navegador/aba).
from lib.warmup import iniciar_aquecimento_em_background

if __name__ != "__main__" or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    iniciar_aquecimento_em_background()


@app.route("/", defaults={"caminho": ""})
@app.route("/<path:caminho>")
def servir_frontend(caminho):
    """Serve o build do React pra qualquer rota que não seja /api/*. Como
    é uma SPA com rotas do lado do cliente (React Router), qualquer
    caminho que não bata com um arquivo real (ex: /campanhas/cadastro)
    devolve o mesmo index.html — o React Router decide o que mostrar a
    partir daí."""
    if caminho.startswith("api/"):
        return jsonify({"erro": "Rota não encontrada."}), 404

    if not FRONTEND_DIST.exists():
        return jsonify({
            "erro": "Build do frontend não encontrado. Rode 'npm run build' dentro de frontend/ "
                    "(em desenvolvimento local, acesse http://localhost:5173 em vez desta porta)."
        }), 404

    caminho_arquivo = FRONTEND_DIST / caminho
    if caminho and caminho_arquivo.is_file():
        return send_from_directory(FRONTEND_DIST, caminho)

    return send_from_directory(FRONTEND_DIST, "index.html")


if __name__ == "__main__":
    app.run(debug=True, port=8000)
