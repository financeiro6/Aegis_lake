import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import api from "../api/client";
import PageHeader from "../components/PageHeader";
import MultiSelectDropdown from "../components/MultiSelectDropdown";
import Modal from "../components/Modal";
import { Plus, Trash, Settings, DollarSign, Download, Refresh } from "../components/icons";
import { brl, percentual } from "../utils/format";
import { VALORES_ABERTOS_ATIVO } from "../config/funcionalidades";
import ImportacaoCampanhas from "../components/ImportacaoCampanhas";
import { useAuth } from "../context/AuthContext";

const STATUS_OPCOES = ["Vigente", "Finalizada", "Em Apuração"];

const FORM_VAZIO = {
  banco: "",
  campanha: "",
  data_inicio: "",
  data_fim: "",
  base_producao: "liquido",
  faixas_metas: [{ faixa: "", meta: "" }],
  filtro_map_indicado: [],
  filtro_map_convenio: [],
  filtro_map_produto: [],
};

async function buscarCampanhas() {
  const { data } = await api.get("/campanhas");
  return data;
}

async function buscarBancos() {
  const { data } = await api.get("/bancos");
  return data;
}

async function buscarCategoriasValoresAbertos() {
  const { data } = await api.get("/valores-abertos/categorias");
  return data;
}

async function buscarValoresMapeados() {
  const { data } = await api.get("/manutencao/valores-mapeados");
  return data;
}

async function buscarAtingimento() {
  const { data } = await api.get("/campanhas/atingimento");
  return data;
}

export default function CampanhasCadastro() {
  const queryClient = useQueryClient();
  const { usuario } = useAuth();
  const [form, setForm] = useState(FORM_VAZIO);
  const [editandoId, setEditandoId] = useState(null);
  const [mensagem, setMensagem] = useState(null);
  const [modalValoresAbertos, setModalValoresAbertos] = useState(null);
  const [formValoresAbertos, setFormValoresAbertos] = useState(null);
  const [mensagemValoresAbertos, setMensagemValoresAbertos] = useState(null);
  const [modalRenovar, setModalRenovar] = useState(null);
  const [formRenovar, setFormRenovar] = useState({ data_inicio: "", data_fim: "" });
  const [mensagemRenovar, setMensagemRenovar] = useState(null);

  const { data: campanhas = [], isLoading, isError, error } = useQuery({
    queryKey: ["campanhas"],
    queryFn: buscarCampanhas,
  });
  const { data: bancos = [] } = useQuery({ queryKey: ["bancos"], queryFn: buscarBancos });
  const { data: categoriasValoresAbertos = [] } = useQuery({
    queryKey: ["valores-abertos-categorias"],
    queryFn: buscarCategoriasValoresAbertos,
    enabled: VALORES_ABERTOS_ATIVO,
  });
  const { data: valoresMapeados = { map_indicado: [], map_convenio: [], map_produto: [] } } = useQuery({
    queryKey: ["valores-mapeados"],
    queryFn: buscarValoresMapeados,
  });
  const { data: atingimento = [] } = useQuery({
    queryKey: ["campanhas-atingimento-cadastro"],
    queryFn: buscarAtingimento,
  });

  function invalidarCampanhas() {
    queryClient.invalidateQueries({ queryKey: ["campanhas"] });
  }

  const criarMutation = useMutation({
    mutationFn: (dados) => api.post("/campanhas", dados),
    onSuccess: () => {
      invalidarCampanhas();
      setMensagem({ ok: true, texto: `Campanha "${form.campanha}" salva.` });
      setForm(FORM_VAZIO);
      setEditandoId(null);
    },
    onError: (err) => setMensagem({ ok: false, texto: err?.response?.data?.erro || err.message }),
  });

  const editarMutation = useMutation({
    mutationFn: ({ id, dados }) => api.put(`/campanhas/${id}`, dados),
    onSuccess: () => {
      invalidarCampanhas();
      setMensagem({ ok: true, texto: `Campanha "${form.campanha}" salva.` });
      setForm(FORM_VAZIO);
      setEditandoId(null);
    },
    onError: (err) => setMensagem({ ok: false, texto: err?.response?.data?.erro || err.message }),
  });

  const excluirMutation = useMutation({
    mutationFn: (id) => api.delete(`/campanhas/${id}`),
    onSuccess: () => {
      invalidarCampanhas();
      setMensagem({ ok: true, texto: "Campanha removida." });
    },
    onError: (err) => setMensagem({ ok: false, texto: err?.response?.data?.erro || err.message }),
  });

  const statusMutation = useMutation({
    mutationFn: ({ id, status }) => api.put(`/campanhas/${id}/status`, { status }),
    onSuccess: () => {
      invalidarCampanhas();
      setMensagem({ ok: true, texto: "Status atualizado." });
    },
    onError: (err) => setMensagem({ ok: false, texto: err?.response?.data?.erro || err.message }),
  });

  const criarValorAbertoMutation = useMutation({
    mutationFn: (dados) => api.post("/valores-abertos", dados),
    onSuccess: () => {
      setMensagemValoresAbertos({ ok: true, texto: "Lançamento adicionado aos valores em aberto." });
    },
    onError: (err) => setMensagemValoresAbertos({ ok: false, texto: err?.response?.data?.erro || err.message }),
  });

  const renovarMutation = useMutation({
    mutationFn: ({ id, dados }) => api.post(`/campanhas/${id}/renovar`, dados),
    onSuccess: () => {
      invalidarCampanhas();
      setMensagemRenovar({ ok: true, texto: "Campanha renovada — critérios e faixas foram copiados pra nova campanha." });
      setTimeout(fecharModalRenovar, 1500);
    },
    onError: (err) => setMensagemRenovar({ ok: false, texto: err?.response?.data?.erro || err.message }),
  });

  function abrirModalRenovar(campanha) {
    setModalRenovar(campanha);
    setFormRenovar({ data_inicio: "", data_fim: "" });
    setMensagemRenovar(null);
  }

  function fecharModalRenovar() {
    setModalRenovar(null);
    setMensagemRenovar(null);
  }

  function handleConfirmarRenovar(e) {
    e.preventDefault();
    if (!formRenovar.data_inicio || !formRenovar.data_fim) {
      setMensagemRenovar({ ok: false, texto: "Informe a nova data de início e de fim da apuração." });
      return;
    }
    renovarMutation.mutate({ id: modalRenovar.id, dados: formRenovar });
  }

  function abrirModalValoresAbertos(campanha) {
    const cenario = atingimento.find((a) => a.id === campanha.id);
    const valorPrevisto = cenario ? cenario.valor_campanha_previsto : 0;
    setModalValoresAbertos(campanha);
    setFormValoresAbertos({
      banco: campanha.banco || "",
      categoria: "Campanha",
      periodo_ref: campanha.campanha || "",
      valor: valorPrevisto,
      data_prevista: campanha.data_fim || "",
      campanha_id: campanha.id,
    });
    setMensagemValoresAbertos(null);
  }

  function fecharModalValoresAbertos() {
    setModalValoresAbertos(null);
    setFormValoresAbertos(null);
    setMensagemValoresAbertos(null);
  }

  const [baixandoApuracaoId, setBaixandoApuracaoId] = useState(null);

  async function handleBaixarApuracao(campanha) {
    setBaixandoApuracaoId(campanha.id);
    try {
      const response = await api.get(`/campanhas/${campanha.id}/relatorio-apuracao/download`, { responseType: "blob" });
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement("a");
      link.href = url;
      const nomeArquivo = campanha.campanha.replace(/[^a-zA-Z0-9]/g, "_");
      const dataStr = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
      link.setAttribute("download", `apuracao_${nomeArquivo}_${dataStr}.xlsx`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setMensagem({ ok: false, texto: err?.response?.data?.erro || "Não foi possível gerar o relatório." });
    } finally {
      setBaixandoApuracaoId(null);
    }
  }

  function handleSalvarValorAberto(e) {
    e.preventDefault();
    if (!formValoresAbertos.banco || !formValoresAbertos.categoria || formValoresAbertos.valor == null || !formValoresAbertos.data_prevista) {
      setMensagemValoresAbertos({ ok: false, texto: "Preencha Banco, Categoria, Valor e Data prevista." });
      return;
    }
    criarValorAbertoMutation.mutate(formValoresAbertos);
  }

  function handleChange(campo, valor) {
    setForm((f) => ({ ...f, [campo]: valor }));
  }

  function handleChangeFaixaMeta(index, campo, valor) {
    setForm((f) => {
      const copia = [...f.faixas_metas];
      copia[index] = { ...copia[index], [campo]: valor };
      return { ...f, faixas_metas: copia };
    });
  }

  function handleAdicionarFaixa() {
    setForm((f) => ({ ...f, faixas_metas: [...f.faixas_metas, { faixa: "", meta: "" }] }));
  }

  function handleRemoverFaixa(index) {
    setForm((f) => ({ ...f, faixas_metas: f.faixas_metas.filter((_, i) => i !== index) }));
  }

  function handleSalvar(e) {
    e.preventDefault();
    if (!form.banco || !form.campanha) {
      setMensagem({ ok: false, texto: "Preencha ao menos Banco e Campanha." });
      return;
    }
    const faixasMetas = form.faixas_metas
      .filter((fm) => fm.faixa !== "" || fm.meta !== "")
      .map((fm) => ({ faixa: fm.faixa === "" ? null : Number(fm.faixa), meta: fm.meta === "" ? null : Number(fm.meta) }));

    const dados = {
      banco: form.banco,
      campanha: form.campanha,
      data_inicio: form.data_inicio,
      data_fim: form.data_fim,
      base_producao: form.base_producao,
      faixas_metas: faixasMetas,
      filtro_map_indicado: form.filtro_map_indicado,
      filtro_map_convenio: form.filtro_map_convenio,
      filtro_map_produto: form.filtro_map_produto,
    };

    if (editandoId) {
      editarMutation.mutate({ id: editandoId, dados: { ...dados, status: form.status || "Vigente" } });
    } else {
      criarMutation.mutate(dados);
    }
  }

  function handleEditar(c) {
    setEditandoId(c.id);
    setForm({
      banco: c.banco || "",
      campanha: c.campanha || "",
      data_inicio: c.data_inicio || "",
      data_fim: c.data_fim || "",
      status: c.status,
      base_producao: c.base_producao || "liquido",
      faixas_metas: c.faixas_metas && c.faixas_metas.length ? c.faixas_metas.map((fm) => ({ faixa: fm.faixa ?? "", meta: fm.meta ?? "" })) : [{ faixa: "", meta: "" }],
      filtro_map_indicado: c.filtro_map_indicado || [],
      filtro_map_convenio: c.filtro_map_convenio || [],
      filtro_map_produto: c.filtro_map_produto || [],
    });
  }

  function handleCancelar() {
    setEditandoId(null);
    setForm(FORM_VAZIO);
  }

  const salvando = criarMutation.isPending || editarMutation.isPending;

  return (
    <div className="fade-in">
      <PageHeader icon={<Plus />} title="Cadastro" subtitle="Cadastre, edite ou remova campanhas e seus bônus por faixa." />

      {usuario?.papel === "admin" && <ImportacaoCampanhas />}

      {mensagem && (
        <div className={`fade-in ${mensagem.ok ? "card status-card-ok" : "card error-card"}`}>
          {mensagem.ok ? (
            <p style={{ margin: 0 }}><span className="status-dot ok" />{mensagem.texto}</p>
          ) : (
            <><p className="error-title">Não foi possível salvar</p><p className="muted small">{mensagem.texto}</p></>
          )}
        </div>
      )}

      <form onSubmit={handleSalvar} className="card card-accent-blue">
        <p className="section-title" style={{ marginTop: 0 }}>{editandoId ? `Editando "${form.campanha}"` : "Nova campanha"}</p>

        <div className="filter-grid">
          <div className="form-row">
            <label>Banco</label>
            <select value={form.banco} onChange={(e) => handleChange("banco", e.target.value)}>
              <option value="">Selecione…</option>
              {bancos.map((b) => <option key={b.valor} value={b.valor}>{b.rotulo}</option>)}
            </select>
            <span className="field-hint">Obrigatório</span>
          </div>
          <div className="form-row">
            <label>Campanha</label>
            <input type="text" value={form.campanha} onChange={(e) => handleChange("campanha", e.target.value)} placeholder="Nome da campanha" />
            <span className="field-hint">Obrigatório</span>
          </div>
          <div className="form-row">
            <label>Data início</label>
            <input type="date" value={form.data_inicio || ""} onChange={(e) => handleChange("data_inicio", e.target.value)} />
          </div>
          <div className="form-row">
            <label>Data fim</label>
            <input type="date" value={form.data_fim || ""} onChange={(e) => handleChange("data_fim", e.target.value)} />
          </div>
          <div className="form-row">
            <label>Base da produção</label>
            <select value={form.base_producao} onChange={(e) => handleChange("base_producao", e.target.value)}>
              <option value="liquido">Líquido (vlr_liquido)</option>
              <option value="bruto">Bruto (vlr_bruto)</option>
            </select>
          </div>
        </div>

        <p className="section-label" style={{ marginTop: "1rem" }}>Faixas e metas (bônus por faixa atingida)</p>
        {form.faixas_metas.map((fm, index) => (
          <div className="filter-grid fade-in" key={index} style={{ marginBottom: "0.5rem", alignItems: "flex-end" }}>
            <div className="form-row">
              <label>Meta {index + 1} (R$ de produção)</label>
              <input type="number" value={fm.meta} onChange={(e) => handleChangeFaixaMeta(index, "meta", e.target.value)} placeholder="Ex: 100000000" />
            </div>
            <div className="form-row">
              <label>Faixa {index + 1} (% de bônus)</label>
              <input type="number" step="0.01" value={fm.faixa} onChange={(e) => handleChangeFaixaMeta(index, "faixa", e.target.value)} placeholder="Ex: 0.30" />
            </div>
            <div className="form-row form-row-action">
              <label>&nbsp;</label>
              <button type="button" className="btn-danger" onClick={() => handleRemoverFaixa(index)} disabled={form.faixas_metas.length === 1}>
                <Trash />
              </button>
            </div>
          </div>
        ))}
        <button type="button" className="btn-link" onClick={handleAdicionarFaixa} style={{ marginBottom: "1rem" }}>
          <Plus /> Adicionar faixa
        </button>

        <p className="section-label" style={{ marginTop: "1rem" }}>
          Qual produção conta pro atingimento de meta (opcional)
        </p>
        <div className="filter-grid">
          <div className="form-row">
            <label>Indicado</label>
            <MultiSelectDropdown
              options={valoresMapeados.map_indicado}
              selected={form.filtro_map_indicado}
              onChange={(v) => setForm((f) => ({ ...f, filtro_map_indicado: v }))}
            />
          </div>
          <div className="form-row">
            <label>Convênio</label>
            <MultiSelectDropdown
              options={valoresMapeados.map_convenio}
              selected={form.filtro_map_convenio}
              onChange={(v) => setForm((f) => ({ ...f, filtro_map_convenio: v }))}
            />
          </div>
          <div className="form-row">
            <label>Produto</label>
            <MultiSelectDropdown
              options={valoresMapeados.map_produto}
              selected={form.filtro_map_produto}
              onChange={(v) => setForm((f) => ({ ...f, filtro_map_produto: v }))}
            />
          </div>
        </div>

        <div className="filter-actions" style={{ marginTop: "1rem" }}>
          <button type="submit" disabled={salvando}>
            <Plus /> {salvando ? "Salvando…" : "Salvar"}
          </button>
          {editandoId && (
            <button type="button" className="btn-link" onClick={handleCancelar}>Cancelar edição</button>
          )}
        </div>
      </form>

      {isLoading && <div className="skeleton-block" />}
      {isError && (
        <div className="card error-card fade-in">
          <p className="error-title">Não deu para consultar o BigQuery</p>
          <p className="muted small">{error?.response?.data?.erro || error?.message}</p>
        </div>
      )}

      {!isLoading && !isError && (
        <div className="card table-wrap fade-in">
          <table>
            <thead>
              <tr><th>Banco</th><th>Campanha</th><th>Período</th><th>Metas → faixas</th><th>Status</th><th></th></tr>
            </thead>
            <tbody>
              {campanhas.length === 0 && (
                <tr><td colSpan={6} className="muted center">Nenhuma campanha cadastrada ainda.</td></tr>
              )}
              {campanhas.map((c) => (
                <tr key={c.id}>
                  <td className="small">{c.banco}</td>
                  <td className="small">{c.campanha}</td>
                  <td className="mono small">{c.data_inicio || ""} — {c.data_fim || ""}</td>
                  <td className="mono small">
                    {(c.faixas_metas || []).map((fm) => `${brl(fm.meta)} → ${percentual(fm.faixa)}`).join(" | ") || "—"}
                  </td>
                  <td className="small">
                    <select
                      value={c.status || "Vigente"}
                      onChange={(e) => statusMutation.mutate({ id: c.id, status: e.target.value })}
                      style={{ minWidth: 130 }}
                    >
                      {STATUS_OPCOES.map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    <button className="btn-link" onClick={() => handleEditar(c)} title="Editar" style={{ marginRight: "0.4rem" }}>
                      <Settings />
                    </button>
                    {VALORES_ABERTOS_ATIVO && (
                      <button className="btn-link" onClick={() => abrirModalValoresAbertos(c)} title="Adicionar aos valores em aberto" style={{ marginRight: "0.4rem" }}>
                        <DollarSign />
                      </button>
                    )}
                    <button
                      className="btn-link"
                      onClick={() => handleBaixarApuracao(c)}
                      title="Baixar relatório de apuração por proposta"
                      disabled={baixandoApuracaoId === c.id}
                      style={{ marginRight: "0.4rem" }}
                    >
                      <Download />
                    </button>
                    <button className="btn-link" onClick={() => abrirModalRenovar(c)} title="Renovar campanha (cria uma nova campanha copiando critérios e faixas)" style={{ marginRight: "0.4rem" }}>
                      <Refresh />
                    </button>
                    <button className="btn-danger" onClick={() => { if (window.confirm(`Remover a campanha "${c.campanha}"?`)) excluirMutation.mutate(c.id); }} title="Excluir">
                      <Trash />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {modalValoresAbertos && formValoresAbertos && (
        <Modal onClose={fecharModalValoresAbertos} width={480}>
          <p className="section-title" style={{ marginTop: 0 }}>
            Adicionar aos valores em aberto — {modalValoresAbertos.campanha}
          </p>

          {mensagemValoresAbertos && (
            <div className={mensagemValoresAbertos.ok ? "card status-card-ok" : "card error-card"} style={{ marginBottom: "1rem" }}>
              {mensagemValoresAbertos.ok ? (
                <p style={{ margin: 0 }}><span className="status-dot ok" />{mensagemValoresAbertos.texto}</p>
              ) : (
                <><p className="error-title">Não foi possível salvar</p><p className="muted small">{mensagemValoresAbertos.texto}</p></>
              )}
            </div>
          )}

          <form onSubmit={handleSalvarValorAberto}>
            <table className="form-table">
              <tbody>
                <tr>
                  <td className="form-table-label">Banco</td>
                  <td>
                    <select value={formValoresAbertos.banco} onChange={(e) => setFormValoresAbertos((f) => ({ ...f, banco: e.target.value }))}>
                      <option value="">Selecione…</option>
                      {bancos.map((b) => <option key={b.valor} value={b.valor}>{b.rotulo}</option>)}
                    </select>
                  </td>
                </tr>
                <tr>
                  <td className="form-table-label">Categoria</td>
                  <td>
                    <select value={formValoresAbertos.categoria} onChange={(e) => setFormValoresAbertos((f) => ({ ...f, categoria: e.target.value }))}>
                      {categoriasValoresAbertos.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </td>
                </tr>
                <tr>
                  <td className="form-table-label">Período de ref.</td>
                  <td><input type="text" value={formValoresAbertos.periodo_ref} onChange={(e) => setFormValoresAbertos((f) => ({ ...f, periodo_ref: e.target.value }))} /></td>
                </tr>
                <tr>
                  <td className="form-table-label">Valor previsto</td>
                  <td>
                    <input type="text" value={brl(formValoresAbertos.valor)} disabled title="Valor previsto atual da campanha (não editável) — vem do cenário de Projeção da Visão geral." />
                  </td>
                </tr>
                <tr>
                  <td className="form-table-label">Data prevista</td>
                  <td><input type="date" value={formValoresAbertos.data_prevista || ""} onChange={(e) => setFormValoresAbertos((f) => ({ ...f, data_prevista: e.target.value }))} /></td>
                </tr>
              </tbody>
            </table>

            <div className="filter-actions" style={{ marginTop: "1rem" }}>
              <button type="submit" disabled={criarValorAbertoMutation.isPending}>
                <Plus /> {criarValorAbertoMutation.isPending ? "Salvando…" : "Adicionar"}
              </button>
              <button type="button" className="btn-link" onClick={fecharModalValoresAbertos}>Fechar</button>
            </div>
          </form>
        </Modal>
      )}

      {modalRenovar && (
        <Modal onClose={fecharModalRenovar} width={420}>
          <p className="section-title" style={{ marginTop: 0 }}>
            Renovar campanha — {modalRenovar.campanha}
          </p>

          {mensagemRenovar && (
            <div className={mensagemRenovar.ok ? "card status-card-ok" : "card error-card"} style={{ marginBottom: "1rem" }}>
              {mensagemRenovar.ok ? (
                <p style={{ margin: 0 }}><span className="status-dot ok" />{mensagemRenovar.texto}</p>
              ) : (
                <><p className="error-title">Não foi possível renovar</p><p className="muted small">{mensagemRenovar.texto}</p></>
              )}
            </div>
          )}

          <form onSubmit={handleConfirmarRenovar}>
            <table className="form-table">
              <tbody>
                <tr>
                  <td className="form-table-label">Nova data início</td>
                  <td><input type="date" value={formRenovar.data_inicio} onChange={(e) => setFormRenovar((f) => ({ ...f, data_inicio: e.target.value }))} /></td>
                </tr>
                <tr>
                  <td className="form-table-label">Nova data fim</td>
                  <td><input type="date" value={formRenovar.data_fim} onChange={(e) => setFormRenovar((f) => ({ ...f, data_fim: e.target.value }))} /></td>
                </tr>
              </tbody>
            </table>

            <div className="filter-actions" style={{ marginTop: "1rem" }}>
              <button type="submit" disabled={renovarMutation.isPending}>
                <Refresh /> {renovarMutation.isPending ? "Renovando…" : "Renovar campanha"}
              </button>
              <button type="button" className="btn-link" onClick={fecharModalRenovar}>Fechar</button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
