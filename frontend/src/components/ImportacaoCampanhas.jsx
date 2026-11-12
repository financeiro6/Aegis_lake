import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "../api/client";
import { dataBr } from "../utils/format";
import { Upload, Trash } from "./icons";

/**
 * Importação em lote de campanhas + critérios (só admin), a partir de uma
 * planilha com as abas "Campanhas" e "Critérios" no formato do Aegis.
 * Fluxo: escolher arquivo → ver prévia → confirmar. Cada importação vira
 * um lote que pode ser desfeito inteiro.
 */
export default function ImportacaoCampanhas() {
  const queryClient = useQueryClient();
  const [arquivo, setArquivo] = useState(null);
  const [previa, setPrevia] = useState(null);
  const [mensagem, setMensagem] = useState(null);

  const { data: lotes = [] } = useQuery({
    queryKey: ["campanhas-importacoes"],
    queryFn: async () => (await api.get("/campanhas/importacao")).data,
  });

  const corpo = () => {
    const fd = new FormData();
    fd.append("arquivo", arquivo);
    return fd;
  };
  const erroDe = (err) => err?.response?.data?.erro || err.message;
  const atualizarTudo = () => {
    ["campanhas", "criterios", "campanhas-atingimento", "campanhas-importacoes"].forEach((k) =>
      queryClient.invalidateQueries({ queryKey: [k] })
    );
  };

  const previaMutation = useMutation({
    mutationFn: async () => (await api.post("/campanhas/importacao/previa", corpo())).data,
    onSuccess: (dados) => { setPrevia(dados); setMensagem(null); },
    onError: (err) => { setPrevia(null); setMensagem({ ok: false, texto: erroDe(err) }); },
  });

  const importarMutation = useMutation({
    mutationFn: async () => (await api.post("/campanhas/importacao", corpo())).data,
    onSuccess: (r) => {
      setMensagem({
        ok: true,
        texto: `Importação concluída: ${r.campanhas_criadas} campanha(s) criada(s), ${r.criterios_criados.toLocaleString("pt-BR")} critério(s).`,
      });
      setPrevia(null);
      setArquivo(null);
      atualizarTudo();
    },
    onError: (err) => setMensagem({ ok: false, texto: erroDe(err) }),
  });

  const desfazerMutation = useMutation({
    mutationFn: async (lote) => (await api.delete(`/campanhas/importacao/${encodeURIComponent(lote)}`)).data,
    onSuccess: () => { setMensagem({ ok: true, texto: "Importação desfeita." }); atualizarTudo(); },
    onError: (err) => setMensagem({ ok: false, texto: erroDe(err) }),
  });

  function confirmar() {
    if (!previa) return;
    const texto =
      `Importar ${previa.campanhas_novas} campanha(s) nova(s) e ${previa.total_criterios.toLocaleString("pt-BR")} critério(s)?` +
      (previa.campanhas_existentes ? `\n${previa.campanhas_existentes} campanha(s) já existem e vão receber os critérios.` : "");
    if (window.confirm(texto)) importarMutation.mutate();
  }

  function desfazer(lote) {
    if (window.confirm(`Tem certeza que deseja desfazer a importação ${lote.lote}? Os ${lote.criterios} critério(s) e as campanhas criadas por ela serão removidos.`)) {
      desfazerMutation.mutate(lote.lote);
    }
  }

  return (
    <div className="card" style={{ marginBottom: "1.25rem" }}>
      <p className="section-title" style={{ marginTop: 0 }}>Importar campanhas e critérios em lote</p>
      <p className="muted small" style={{ marginTop: 0 }}>
        Planilha .xlsx com as abas “Campanhas” e “Critérios” no formato do Aegis (cabeçalho na linha 3).
        Nada é gravado antes de você confirmar a prévia.
      </p>

      <div className="filter-actions" style={{ marginBottom: "0.75rem" }}>
        <input
          type="file"
          accept=".xlsx"
          onChange={(e) => { setArquivo(e.target.files?.[0] || null); setPrevia(null); setMensagem(null); }}
          style={{ maxWidth: 360 }}
        />
        <button type="button" onClick={() => previaMutation.mutate()} disabled={!arquivo || previaMutation.isPending}>
          {previaMutation.isPending ? "Lendo…" : "Ver prévia"}
        </button>
      </div>

      {mensagem && (
        <div className={mensagem.ok ? "card status-card-ok" : "card error-card"} style={{ marginBottom: "0.75rem" }}>
          {mensagem.ok ? (
            <p style={{ margin: 0 }}><span className="status-dot ok" />{mensagem.texto}</p>
          ) : (
            <><p className="error-title">Não foi possível importar</p><p className="muted small">{mensagem.texto}</p></>
          )}
        </div>
      )}

      {previa && (
        <div className="fade-in">
          <p className="small" style={{ margin: "0 0 0.5rem" }}>
            <strong>{previa.total_campanhas}</strong> campanha(s) ({previa.campanhas_novas} nova(s), {previa.campanhas_existentes} já existente(s)) ·{" "}
            <strong>{previa.total_criterios.toLocaleString("pt-BR")}</strong> critério(s) ({previa.criterios_ativos.toLocaleString("pt-BR")} ativos,{" "}
            {previa.criterios_nao_contabilizar.toLocaleString("pt-BR")} não contabilizar)
            {previa.total_erros > 0 && <> · <span style={{ color: "var(--red)" }}>{previa.total_erros} linha(s) com erro ficarão de fora</span></>}
          </p>
          {previa.ja_importado && (
            <p className="field-error">Este arquivo já foi importado — desfaça a importação anterior antes de importar de novo.</p>
          )}

          <div className="table-wrap" style={{ maxHeight: 260, marginBottom: "0.75rem" }}>
            <table className="tabela-importacao">
              <thead><tr><th>Banco</th><th>Campanha</th><th>Status</th><th>Período</th><th className="align-right">Critérios</th><th>Situação</th></tr></thead>
              <tbody>
                {previa.campanhas.map((c) => (
                  <tr key={`${c.banco}|${c.campanha}`}>
                    <td className="small">{c.banco}</td>
                    <td className="small">{c.campanha}</td>
                    <td className="small">{c.status}</td>
                    <td className="small">{dataBr(c.data_inicio)} a {dataBr(c.data_fim)}</td>
                    <td className="small align-right">{c.criterios.toLocaleString("pt-BR")}</td>
                    <td className="small">{c.ja_existe ? "Já existe — recebe os critérios" : "Nova"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {previa.erros.length > 0 && (
            <details style={{ marginBottom: "0.75rem" }}>
              <summary className="small">Ver linhas com erro ({previa.total_erros})</summary>
              <ul className="small muted">
                {previa.erros.map((e) => <li key={`${e.aba}-${e.linha}`}>Aba {e.aba}, linha {e.linha}: {e.motivo}</li>)}
              </ul>
            </details>
          )}

          <button type="button" onClick={confirmar} disabled={importarMutation.isPending || previa.ja_importado || !previa.total_criterios}>
            <Upload className="btn-icon" /> {importarMutation.isPending ? "Importando…" : "Confirmar importação"}
          </button>
        </div>
      )}

      {lotes.length > 0 && (
        <>
          <p className="section-label" style={{ marginTop: "1.25rem" }}>Importações feitas</p>
          <div className="table-wrap" style={{ maxHeight: 200 }}>
            <table className="tabela-importacao">
              <thead><tr><th>Importação</th><th>Data</th><th>Por</th><th className="align-right">Critérios</th><th></th></tr></thead>
              <tbody>
                {lotes.map((l) => (
                  <tr key={l.lote}>
                    <td className="mono small">{l.lote}</td>
                    <td className="small">{dataBr(l.importado_em)}</td>
                    <td className="small">{l.importado_por}</td>
                    <td className="small align-right">{Number(l.criterios).toLocaleString("pt-BR")}</td>
                    <td style={{ paddingLeft: "1rem", whiteSpace: "nowrap", textAlign: "right" }}>
                      <button type="button" className="btn-danger" onClick={() => desfazer(l)} disabled={desfazerMutation.isPending} title="Desfazer importação">
                        <Trash />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
