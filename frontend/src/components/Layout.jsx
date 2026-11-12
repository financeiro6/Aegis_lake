import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";

import Sidebar from "./Sidebar";
import UserMenu from "./UserMenu";
import { Menu } from "./icons";

export default function Layout() {
  // No celular/tablet (até 900px) o menu lateral vira uma gaveta: fica
  // escondido e abre pelo botão do topo. No computador nada muda — o
  // botão e o fundo escuro só aparecem via CSS abaixo de 900px.
  const [menuAberto, setMenuAberto] = useState(false);
  const location = useLocation();

  useEffect(() => {
    setMenuAberto(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!menuAberto) return undefined;
    const aoTeclar = (e) => {
      if (e.key === "Escape") setMenuAberto(false);
    };
    document.addEventListener("keydown", aoTeclar);
    document.body.classList.add("sem-rolagem");
    return () => {
      document.removeEventListener("keydown", aoTeclar);
      document.body.classList.remove("sem-rolagem");
    };
  }, [menuAberto]);

  return (
    <div className="app-shell">
      <Sidebar aberta={menuAberto} aoFechar={() => setMenuAberto(false)} />
      {menuAberto && <div className="sidebar-fundo" onClick={() => setMenuAberto(false)} aria-hidden="true" />}
      <div className="main-area">
        <div className="topbar">
          <button
            type="button"
            className="topbar-menu"
            onClick={() => setMenuAberto(true)}
            aria-label="Abrir menu"
            aria-expanded={menuAberto}
          >
            <Menu />
          </button>
          <UserMenu />
        </div>
        <Outlet />
      </div>
    </div>
  );
}
