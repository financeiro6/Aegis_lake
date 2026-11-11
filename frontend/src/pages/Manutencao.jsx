import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import api from "../api/client";
import PageHeader from "../components/PageHeader";
import { Settings } from "../components/icons";
import { VALORES_ABERTOS_ATIVO } from "../config/funcionalidades";

export default function Manutencao() {
  const [resultado, setResultado] = useState(null);
  const [resultadoSync, setResultadoSync] = useState(null);

  const cruzarIndicadoMutation = useMutation({
    mutationFn: () => api.post("/manutencao/cruzar-indicado"),
    onSuccess: (res) => setResultado({ ok: true, ...res.data }),
    onError: (err) => setResultado({ ok: false, erro: err?.response?.data?.erro || err.message }),
  });

  const sincronizarValoresCampanhasMutation = useMutation({
    mutationFn: () => api.post("/manutencao/sincronizar-valores-campanhas"),
    onSuccess: (res) => setResultadoSync({ ok: true, ...res.data }),
    onError: (err) => setResultadoSync({ ok: false, erro: err?.response?.data?.erro || err.message }),
  });

  return (
    <div className="fade-in">
      <PageHeader icon={<Settings />} title="Manutenção" subtitle="Ferramentas administrativas para operações de dados." />

      {resultado && (
        <div className={`fade-in ${resultado.ok ? "card status-card-ok" : "card error-card"}`}>
          {resultado.ok ? (
            <>
              <p style={{ margin: "0 0 0.4rem" }}><span className="status-dot ok" />Cruzamento concluído.</p>
              <p className="muted small" style={{ margin: 0 }}>
                {resultado.codigos_considerados} código(s) de indicado considerado(s).{" "}
                {resultado.linhas_atualizadas != null
                  ? `${resultado.linhas_atualizadas} linha(s) atualizada(s).`
                  : "Tabela atualizada."}
              </p>
            </>
          ) : (
            <>
              <p className="error-title">Não foi possível concluir</p>
              <p className="muted small">{resultado.erro}</p>
            </>
          )}
        </div>
      )}

      <div className="card">
        <p className="section-title" style={{ marginTop: 0 }}>Map Indicado</p>
        <button type="button" onClick={() => cruzarIndicadoMutation.mutate()} disabled={cruzarIndicadoMutation.isPending}>
          {cruzarIndicadoMutation.isPending ? "Cruzando…" : "Rodar cruzamento — Map Indicado"}
        </button>
      </div>

      <div className="card">
        <p className="section-title" style={{ marginTop: 0 }}>Map Convênio / Map Produto</p>
        <Link to="/admin/mapeamento" className="btn-link">
          <Settings /> Ir para Mapeamento
        </Link>
      </div>

      {VALORES_ABERTOS_ATIVO && (
      <div className="card">
        <p className="section-title" style={{ marginTop: 0 }}>Sincronizar valores de campanhas</p>
        <button
          type="button"
          onClick={() => sincronizarValoresCampanhasMutation.mutate()}
          disabled={sincronizarValoresCampanhasMutation.isPending}
        >
          {sincronizarValoresCampanhasMutation.isPending ? "Sincronizando…" : "Sincronizar valores de campanhas"}
        </button>

        {resultadoSync && (
          <div className={`fade-in ${resultadoSync.ok ? "card status-card-ok" : "card error-card"}`} style={{ marginTop: "1rem" }}>
            {resultadoSync.ok ? (
              <>
                <p style={{ margin: "0 0 0.4rem" }}><span className="status-dot ok" />Sincronização concluída.</p>
                <p className="muted small" style={{ margin: 0 }}>
                  {resultadoSync.verificados} lançamento(s) verificado(s), {resultadoSync.atualizados} atualizado(s).
                  {resultadoSync.campanhas_nao_encontradas > 0
                    ? ` ${resultadoSync.campanhas_nao_encontradas} lançamento(s) apontam pra campanha(s) que não existem mais.`
                    : ""}
                </p>
              </>
            ) : (
              <>
                <p className="error-title">Não foi possível concluir</p>
                <p className="muted small">{resultadoSync.erro}</p>
              </>
            )}
          </div>
        )}
      </div>
      )}
    </div>
  );
}
