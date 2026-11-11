import { Routes, Route } from "react-router-dom";

import Layout from "./components/Layout";
import ProtectedRoute from "./components/ProtectedRoute";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Importar from "./pages/Importar";
import Registros from "./pages/Registros";
import Relatorio from "./pages/Relatorio";
import Parametros from "./pages/Parametros";
import Indicados from "./pages/Indicados";
import CampanhasVisaoGeral from "./pages/CampanhasVisaoGeral";
import CampanhasCadastro from "./pages/CampanhasCadastro";
import CampanhasCriterios from "./pages/CampanhasCriterios";
import CriteriosHistorico from "./pages/CriteriosHistorico";
import AdminUsuarios from "./pages/AdminUsuarios";
import Manutencao from "./pages/Manutencao";
import Mapeamento from "./pages/Mapeamento";
import ValoresAbertosVisaoGeral from "./pages/ValoresAbertosVisaoGeral";
import ValoresAbertosCadastro from "./pages/ValoresAbertosCadastro";
import ValoresAbertosAcompanhamento from "./pages/ValoresAbertosAcompanhamento";
import { VALORES_ABERTOS_ATIVO } from "./config/funcionalidades";

const SOMENTE_EDITOR_ADMIN = ["admin", "editor"];
const VISAO_GERAL = ["admin", "editor", "visualizador"];
const VALORES_ABERTOS_LEITURA = ["admin", "editor", "visualizador", "valores_abertos"];
const VALORES_ABERTOS_ESCRITA = ["admin", "editor", "valores_abertos"];

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />

      <Route
        element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route path="/" element={<ProtectedRoute papeis={VISAO_GERAL}><Dashboard /></ProtectedRoute>} />

        <Route
          path="/importar"
          element={<ProtectedRoute papeis={SOMENTE_EDITOR_ADMIN}><Importar /></ProtectedRoute>}
        />
        <Route path="/logs" element={<ProtectedRoute papeis={SOMENTE_EDITOR_ADMIN}><Registros /></ProtectedRoute>} />
        <Route path="/relatorio" element={<ProtectedRoute papeis={SOMENTE_EDITOR_ADMIN}><Relatorio /></ProtectedRoute>} />
        <Route
          path="/parametros"
          element={<ProtectedRoute papeis={SOMENTE_EDITOR_ADMIN}><Parametros /></ProtectedRoute>}
        />
        <Route
          path="/indicados"
          element={<ProtectedRoute papeis={SOMENTE_EDITOR_ADMIN}><Indicados /></ProtectedRoute>}
        />

        <Route path="/campanhas" element={<ProtectedRoute papeis={VISAO_GERAL}><CampanhasVisaoGeral /></ProtectedRoute>} />
        <Route
          path="/campanhas/cadastro"
          element={<ProtectedRoute papeis={SOMENTE_EDITOR_ADMIN}><CampanhasCadastro /></ProtectedRoute>}
        />
        <Route
          path="/campanhas/criterios"
          element={<ProtectedRoute papeis={SOMENTE_EDITOR_ADMIN}><CampanhasCriterios /></ProtectedRoute>}
        />
        <Route
          path="/campanhas/criterios/historico"
          element={<ProtectedRoute papeis={SOMENTE_EDITOR_ADMIN}><CriteriosHistorico /></ProtectedRoute>}
        />

        <Route
          path="/admin/usuarios"
          element={<ProtectedRoute papeis={["admin"]}><AdminUsuarios /></ProtectedRoute>}
        />
        <Route
          path="/admin/manutencao"
          element={<ProtectedRoute papeis={["admin"]}><Manutencao /></ProtectedRoute>}
        />
        <Route
          path="/admin/mapeamento"
          element={<ProtectedRoute papeis={["admin"]}><Mapeamento /></ProtectedRoute>}
        />

        {VALORES_ABERTOS_ATIVO && (
          <>
            <Route
              path="/valores-abertos"
              element={<ProtectedRoute papeis={VALORES_ABERTOS_LEITURA}><ValoresAbertosVisaoGeral /></ProtectedRoute>}
            />
            <Route
              path="/valores-abertos/cadastro"
              element={<ProtectedRoute papeis={VALORES_ABERTOS_ESCRITA}><ValoresAbertosCadastro /></ProtectedRoute>}
            />
            <Route
              path="/valores-abertos/acompanhamento"
              element={<ProtectedRoute papeis={VALORES_ABERTOS_ESCRITA}><ValoresAbertosAcompanhamento /></ProtectedRoute>}
            />
          </>
        )}
      </Route>
    </Routes>
  );
}
