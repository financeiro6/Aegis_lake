import { useState } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { useAuth } from "../context/AuthContext";
import { APP_VERSION } from "../version";
import {
  Grid,
  Upload,
  ListIcon,
  Download,
  Settings,
  Megaphone,
  Users,
  DollarSign,
  Plus,
  Table,
} from "./icons";
import { VALORES_ABERTOS_ATIVO } from "../config/funcionalidades";

const PRODUCAO_PATHS = ["/", "/importar", "/logs", "/relatorio", "/parametros"];
const CAMPANHAS_PATHS = ["/campanhas", "/campanhas/cadastro", "/campanhas/criterios"];
const VALORES_ABERTOS_PATHS = ["/valores-abertos", "/valores-abertos/cadastro", "/valores-abertos/acompanhamento"];

const PAPEIS_POR_ROTA = {
  "/": ["admin", "editor", "visualizador"],
  "/importar": ["admin", "editor"],
  "/logs": ["admin", "editor"],
  "/relatorio": ["admin", "editor"],
  "/parametros": ["admin", "editor"],
  "/indicados": ["admin", "editor"],
  "/campanhas": ["admin", "editor", "visualizador"],
  "/campanhas/cadastro": ["admin", "editor"],
  "/campanhas/criterios": ["admin", "editor"],
  "/valores-abertos": ["admin", "editor", "visualizador", "valores_abertos"],
  "/valores-abertos/cadastro": ["admin", "editor", "valores_abertos"],
  "/valores-abertos/acompanhamento": ["admin", "editor", "valores_abertos"],
  "/admin/usuarios": ["admin"],
  "/admin/manutencao": ["admin"],
  "/admin/mapeamento": ["admin"],
};

function podeVer(rota, papel) {
  const permitidos = PAPEIS_POR_ROTA[rota];
  if (!permitidos) return true;
  return permitidos.includes(papel);
}

function Item({ to, icon, children, papel }) {
  if (!podeVer(to, papel)) return null;
  return (
    <NavLink to={to} className={({ isActive }) => (isActive ? "active" : "")} end>
      {icon}
      {" "}
      {children}
    </NavLink>
  );
}

export default function Sidebar() {
  const { usuario } = useAuth();
  const papel = usuario?.papel;
  const location = useLocation();

  const [producaoAberto, setProducaoAberto] = useState(PRODUCAO_PATHS.includes(location.pathname));
  const [campanhasAberto, setCampanhasAberto] = useState(CAMPANHAS_PATHS.includes(location.pathname));
  const [valoresAbertosAberto, setValoresAbertosAberto] = useState(VALORES_ABERTOS_PATHS.includes(location.pathname));

  const producaoAtivo = PRODUCAO_PATHS.includes(location.pathname);
  const campanhasAtivo = CAMPANHAS_PATHS.some((p) => location.pathname.startsWith(p));
  const valoresAbertosAtivo = VALORES_ABERTOS_PATHS.includes(location.pathname);

  return (
    <aside className="sidebar">
      <div className="brand">
        <img src="/aegis-logo.png" className="brand-mark-img" alt="Aegis" />
        <div className="brand-name">
          A<span className="brand-name-accent">E</span>GIS
        </div>
      </div>

      <nav className="sidenav">
        <details className="nav-group" open={producaoAberto || producaoAtivo} onToggle={(e) => setProducaoAberto(e.target.open)}>
          <summary className={producaoAtivo ? "has-active" : ""}>
            <Grid /> Produção
          </summary>
          <div className="nav-group-items">
            <Item to="/" icon={<Grid />} papel={papel}>Visão geral</Item>
            <Item to="/importar" icon={<Upload />} papel={papel}>Importar</Item>
            <Item to="/logs" icon={<ListIcon />} papel={papel}>Registros</Item>
            <Item to="/relatorio" icon={<Download />} papel={papel}>Relatório</Item>
            <Item to="/parametros" icon={<Settings />} papel={papel}>Parâmetros</Item>
          </div>
        </details>

        <details className="nav-group" open={campanhasAberto || campanhasAtivo} onToggle={(e) => setCampanhasAberto(e.target.open)}>
          <summary className={campanhasAtivo ? "has-active" : ""}>
            <Megaphone /> Campanhas
          </summary>
          <div className="nav-group-items">
            <Item to="/campanhas" icon={<Grid />} papel={papel}>Visão geral</Item>
            <Item to="/campanhas/cadastro" icon={<Plus />} papel={papel}>Cadastro</Item>
            <Item to="/campanhas/criterios" icon={<Settings />} papel={papel}>Critérios</Item>
          </div>
        </details>

        <Item to="/indicados" icon={<Users />} papel={papel}>Indicados</Item>

        {VALORES_ABERTOS_ATIVO && (
        <details className="nav-group" open={valoresAbertosAberto || valoresAbertosAtivo} onToggle={(e) => setValoresAbertosAberto(e.target.open)}>
          <summary className={valoresAbertosAtivo ? "has-active" : ""}>
            <DollarSign /> Valores em aberto
          </summary>
          <div className="nav-group-items">
            <Item to="/valores-abertos" icon={<Grid />} papel={papel}>Visão geral</Item>
            <Item to="/valores-abertos/cadastro" icon={<Plus />} papel={papel}>Cadastro</Item>
            <Item to="/valores-abertos/acompanhamento" icon={<ListIcon />} papel={papel}>Acompanhamento</Item>
          </div>
        </details>
        )}

        {(papel === "admin") && (
          <>
            <div className="sidenav-divider">ADMIN</div>
            <Item to="/admin/usuarios" icon={<Users />} papel={papel}>Usuários</Item>
            <Item to="/admin/manutencao" icon={<Settings />} papel={papel}>Manutenção</Item>
            <Item to="/admin/mapeamento" icon={<Table />} papel={papel}>Mapeamento</Item>
          </>
        )}
      </nav>

      <div className="sidebar-footer">
        <p className="user-papel" style={{ padding: "0 0.5rem" }}>AEGIS · v{APP_VERSION}</p>
      </div>
    </aside>
  );
}
