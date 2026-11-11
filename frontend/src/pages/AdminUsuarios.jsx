import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import api from "../api/client";
import PageHeader from "../components/PageHeader";
import { Users, Plus, Trash, Settings } from "../components/icons";
import { useAuth } from "../context/AuthContext";
import { VALORES_ABERTOS_ATIVO } from "../config/funcionalidades";

const PAPEL_LABEL = {
  admin: "Admin",
  editor: "Editor",
  visualizador: "Visualizador",
  valores_abertos: "Visualizador de Valores em Aberto",
};

async function buscarUsuarios() {
  const { data } = await api.get("/usuarios");
  return data;
}

export default function AdminUsuarios() {
  const { usuario: usuarioLogado } = useAuth();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [nome, setNome] = useState("");
  const [senha, setSenha] = useState("");
  const [papel, setPapel] = useState("visualizador");
  const [editandoEmail, setEditandoEmail] = useState(null);
  const [mensagem, setMensagem] = useState(null);

  const { data: usuarios = [], isLoading, isError, error } = useQuery({
    queryKey: ["usuarios"],
    queryFn: buscarUsuarios,
  });

  function limparForm() {
    setEmail("");
    setNome("");
    setSenha("");
    setPapel("visualizador");
    setEditandoEmail(null);
  }

  const salvarMutation = useMutation({
    mutationFn: (dados) => api.post("/usuarios", dados),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["usuarios"] });
      setMensagem({ ok: true, texto: `Usuário "${email}" salvo.` });
      limparForm();
    },
    onError: (err) => setMensagem({ ok: false, texto: err?.response?.data?.erro || err.message }),
  });

  const excluirMutation = useMutation({
    mutationFn: (emailAlvo) => api.delete(`/usuarios/${encodeURIComponent(emailAlvo)}`),
    onSuccess: (_, emailAlvo) => {
      queryClient.invalidateQueries({ queryKey: ["usuarios"] });
      setMensagem({ ok: true, texto: `Usuário "${emailAlvo}" removido.` });
      if (emailAlvo === editandoEmail) limparForm();
    },
    onError: (err) => setMensagem({ ok: false, texto: err?.response?.data?.erro || err.message }),
  });

  function handleSalvar(e) {
    e.preventDefault();
    if (!email || !email.includes("@")) {
      setMensagem({ ok: false, texto: "Informe um e-mail válido." });
      return;
    }
    salvarMutation.mutate({ email, nome, senha: senha || undefined, papel });
  }

  function handleEditar(u) {
    setEditandoEmail(u.email);
    setEmail(u.email);
    setNome(u.nome || "");
    setSenha("");
    setPapel(u.papel);
    setMensagem(null);
  }

  const salvando = salvarMutation.isPending;

  return (
    <div className="fade-in">
      <PageHeader icon={<Users />} title="Usuários" subtitle="Quem pode acessar o sistema e com qual permissão." />

      {mensagem && (
        <div className={mensagem.ok ? "card status-card-ok" : "card error-card"}>
          {mensagem.ok ? (
            <p style={{ margin: 0 }}><span className="status-dot ok" />{mensagem.texto}</p>
          ) : (
            <>
              <p className="error-title">Não foi possível salvar</p>
              <p className="muted small">{mensagem.texto}</p>
            </>
          )}
        </div>
      )}

      <form onSubmit={handleSalvar} className="card card-accent-blue" autoComplete="off">
        <p className="section-title" style={{ marginTop: 0 }}>
          {editandoEmail ? `Editando "${editandoEmail}"` : "Novo usuário"}
        </p>
        <div className="filter-grid">
          <div className="form-row">
            <label>E-mail</label>
            <input
              type="email"
              name="novo-usuario-email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="pessoa@empresa.com"
              disabled={!!editandoEmail}
              title={editandoEmail ? "Não é possível trocar o e-mail de um usuário existente" : ""}
              autoComplete="off"
            />
          </div>
          <div className="form-row">
            <label>Nome</label>
            <input
              type="text"
              name="novo-usuario-nome"
              value={nome}
              onChange={(e) => setNome(e.target.value)}
              placeholder="Nome de exibição"
              autoComplete="off"
            />
          </div>
          <div className="form-row">
            <label>Senha (opcional)</label>
            <input
              type="password"
              name="novo-usuario-senha"
              value={senha}
              onChange={(e) => setSenha(e.target.value)}
              placeholder={editandoEmail ? "deixe em branco para manter a atual" : "deixe em branco — a pessoa define no primeiro acesso"}
              autoComplete="new-password"
            />
          </div>
          <div className="form-row">
            <label>Papel</label>
            <select value={papel} onChange={(e) => setPapel(e.target.value)}>
              <option value="admin">Admin</option>
              <option value="editor">Editor</option>
              <option value="visualizador">Visualizador</option>
              {VALORES_ABERTOS_ATIVO && <option value="valores_abertos">Visualizador de Valores em Aberto</option>}
            </select>
          </div>
          <div className="form-row form-row-action">
            <label>&nbsp;</label>
            <div className="filter-actions">
              <button type="submit" disabled={salvando}>
                <Plus /> {salvando ? "Salvando…" : editandoEmail ? "Salvar alterações" : "Adicionar"}
              </button>
              {editandoEmail && (
                <button type="button" className="btn-link" onClick={limparForm}>
                  Cancelar edição
                </button>
              )}
            </div>
          </div>
        </div>
      </form>

      {isLoading && <div className="skeleton-block" />}
      {isError && (
        <div className="card error-card">
          <p className="error-title">Não deu para consultar o BigQuery</p>
          <p className="muted small">{error?.response?.data?.erro || error?.message}</p>
        </div>
      )}

      {!isLoading && !isError && (
        <div className="card table-wrap">
          <table>
            <thead>
              <tr><th>E-mail</th><th>Nome</th><th>Papel</th><th>Status</th><th></th></tr>
            </thead>
            <tbody>
              {usuarios.length === 0 && (
                <tr><td colSpan={5} className="muted center">Nenhum usuário cadastrado ainda.</td></tr>
              )}
              {usuarios.map((u) => {
                const podeExcluir = u.email !== usuarioLogado?.email;
                return (
                  <tr key={u.email}>
                    <td className="small">{u.email}</td>
                    <td className="small">{u.nome}</td>
                    <td className="small">{PAPEL_LABEL[u.papel] || u.papel}</td>
                    <td className="small">
                      <span className={`status-dot ${u.tem_senha ? "ok" : "warn"}`} />
                      {u.tem_senha ? "Ativo" : "Aguardando primeiro acesso"}
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <button
                        className="btn-link"
                        title="Editar"
                        style={{ marginRight: "0.4rem" }}
                        onClick={() => handleEditar(u)}
                      >
                        <Settings />
                      </button>
                      <button
                        className="btn-danger"
                        disabled={!podeExcluir}
                        title={podeExcluir ? "Excluir" : "Você não pode excluir seu próprio usuário"}
                        onClick={() => excluirMutation.mutate(u.email)}
                      >
                        <Trash />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
