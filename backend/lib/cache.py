import functools
import logging
import threading
import time

log = logging.getLogger("cache")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

_store = {}
_lock = threading.Lock()


def _freeze(value):
    """Converte listas/dicts em algo hasheável, para usar como parte da
    chave do cache (ex: a lista de meses selecionados no filtro)."""
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    return value


def cached(ttl_seconds=None):
    """Decorator: cacheia o retorno da função em memória, usando os
    argumentos como parte da chave. Pensado para consultas de leitura ao
    BigQuery que são repetidas toda vez que a pessoa navega entre páginas.

    ttl_seconds=None (padrão): sem expiração por tempo — a consulta roda
    uma vez e o resultado fica valendo até alguém escrever algo (que limpa
    tudo via invalidar_tudo()) ou clicar em "Atualizar agora" na tela.
    Passe um número se quiser voltar a expirar por tempo em algum caso
    específico."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = (func.__module__, func.__qualname__, _freeze(args), _freeze(kwargs))
            now = time.time()

            with _lock:
                entry = _store.get(key)
                if entry and (ttl_seconds is None or (now - entry[0]) < ttl_seconds):
                    log.info("CACHE HIT  %s args=%s kwargs=%s", func.__qualname__, args, kwargs)
                    return entry[1]

            log.info("CACHE MISS %s args=%s kwargs=%s (vai consultar o BigQuery)", func.__qualname__, args, kwargs)
            resultado = func(*args, **kwargs)

            with _lock:
                _store[key] = (now, resultado)

            return resultado

        return wrapper

    return decorator


def invalidar_tudo():
    """Limpa todo o cache. Chamado sempre que algo escreve na base
    (importação, exclusão de log, adicionar/excluir indicado), para nunca
    mostrar dado desatualizado depois de uma mudança."""
    with _lock:
        qtd = len(_store)
        _store.clear()
    log.info("CACHE INVALIDADO — %s entradas removidas", qtd)

    for callback in list(_ao_invalidar):
        try:
            callback()
        except Exception:  # noqa: BLE001
            log.exception("Falha ao avisar %s sobre a invalidação do cache", callback)


_ao_invalidar = []


def registrar_ao_invalidar(callback):
    """Registra uma função chamada logo depois de invalidar_tudo() — usada
    pelo resumo em memória (lib/resumo.py) para se recarregar quando algo
    escreve na base."""
    if callback not in _ao_invalidar:
        _ao_invalidar.append(callback)
