# Aegis (React + Flask)

Migração completa pra React, resolvendo de vez o problema de recarregar
dados ao trocar de página. A peça-chave é o **TanStack Query**: cada
consulta fica guardada na memória do navegador (por aba), e só é refeita
quando você manda — sem depender de nenhum truque de timing como
acontecia nas tentativas anteriores.

## Arquitetura

```
taurus/
  backend/     → API Flask (JSON puro) — toda a lógica de negócio
  frontend/    → React + Vite + TanStack Query
```

- **Backend**: reaproveita 100% do `lib/*.py` que já foi testado
  extensivamente (BigQuery, validação de importação, dedup, regras de
  mapeamento, CRUD de campanhas/usuários). A única mudança é que as rotas
  agora devolvem JSON em vez de HTML renderizado.
- **Frontend**: cada tela busca dados via TanStack Query. Uma vez
  buscado, o dado fica na memória da aba — trocar de tela e voltar é
  instantâneo. Query keys incluem os filtros (ex: `["dashboard", banco,
  mesInicio, mesFim]`), então cada combinação de filtro fica cacheada
  separadamente; o botão "Atualizar agora" força uma busca nova
  (`invalidateQueries`).
- Os dois se falam via **proxy do Vite** (`/api/*` → `localhost:8000`) —
  isso faz o navegador achar que é tudo a mesma origem, então cookies de
  sessão funcionam normalmente e não tem CORS pra configurar.
- **Responsivo** (bloco "Responsivo" no fim de `frontend/src/style.css`):
  até 900px o menu lateral vira gaveta (botão no topo, fecha ao navegar,
  no fundo escuro ou com Esc), tabelas largas rolam dentro do card e os
  cartões de valores quebram de linha; até 600px filtros ficam em pares
  (texto livre na linha inteira), campos com 16px (sem zoom no iPhone) e
  janelas sobem de baixo ocupando a largura. Acima de 900px nada muda.
- **Resumo em memória do Dashboard** (`backend/lib/resumo.py`): uma
  consulta só agrupa a base por dia × banco × convênio × produto (últimos
  `RESUMO_MESES` meses, padrão 36) e guarda o resultado na memória do
  servidor. Gráfico, árvore, projeção e lista de meses saem daí, sem ir ao
  BigQuery a cada filtro. É recarregado na subida, depois de qualquer
  gravação (`invalidar_tudo()`, em segundo plano) e a cada
  `RESUMO_TTL_MIN` minutos (padrão 30). Período anterior à janela, falha
  na carga ou resumo acima de `RESUMO_MAX_LINHAS` linhas (padrão 1,5
  milhão) → as telas voltam a consultar o BigQuery direto.

## Como rodar (dois terminais)

**Terminal 1 — backend:**
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env.local
```

Preencha o `.env.local` com as mesmas variáveis de sempre
(`BIGQUERY_PROJECT_ID`, `GOOGLE_APPLICATION_CREDENTIALS` ou
`GOOGLE_CREDENTIALS_JSON`, `FLASK_SECRET_KEY`, `ADMIN_BOOTSTRAP_EMAIL`,
`ADMIN_BOOTSTRAP_PASSWORD`).

```bash
python app.py
```

Backend sobe em `http://localhost:8000`.

**Terminal 2 — frontend:**
```bash
cd frontend
npm install
```

Coloque seu logo em `frontend/public/grupo_nova_icon.png` (veja
`frontend/public/LEIA-ME.txt`).

```bash
npm run dev
```

Frontend sobe em `http://localhost:5173` — **é esse endereço que você
acessa no navegador**, não o :8000.

## O que já está pronto

- **Importação em lote de campanhas e critérios** (Campanhas → Cadastro,
  só admin; `backend/lib/importacao_campanhas.py`): planilha .xlsx com as
  abas "Campanhas" e "Critérios" (cabeçalho na linha 3, mesmas colunas da
  tela). Mostra prévia (novas × já existentes, linhas com erro) e só grava
  ao confirmar — uma carga por tabela no BigQuery. Tudo recebe o mesmo
  `lote_importacao` e pode ser desfeito inteiro; o mesmo arquivo não entra
  duas vezes. As regras de cálculo não mudam: cada linha é um critério
  igual ao cadastrado pela tela. Para aguentar milhares de critérios, a
  escolha do critério de cada contrato olha só os critérios da tabela do
  contrato + os sem tabela (mesmo resultado, `_indexar_criterios`), e a
  janela de critérios lista 200 por vez com busca.
- **Design system novo**: Tailwind v4 + tokens em OKLCH + fontes (Space
  Grotesk para títulos, DM Sans para o corpo, JetBrains Mono para
  números/código) — ver `frontend/src/theme.css`. O `style.css` original
  vira uma "ponte": os mesmos nomes de variável de sempre (`--primary`,
  `--surface`, `--ink` etc.) agora apontam pro tema novo, então todo o
  resto do app herdou a paleta/fonte sem precisar reescrever cada tela
- Sistema de transições/animações (fade-in, skeleton loading, hover,
  abrir/fechar suave) aplicado em todo o app
- Login (e-mail + senha, sessão via cookie) + gestão de usuários com 3
  papéis (Admin / Editor / Visualizador), incluindo editar usuário existente
  e redefinir a própria senha (menu no canto superior direito)
- Visão geral (dashboard) — gráfico, KPIs, acordeão por banco/convênio/produto
- Importar — upload de arquivo de produção, com todo o diagnóstico de
  colunas ausentes/obrigatórias/opcionais (Editor/Admin)
- Registros (Logs) — histórico de importações, com busca, exclusão de lote e
  **auditoria completa** (quem importou, quem excluiu e quando — depois de
  excluído, a linha mostra "Excluído por X" no lugar do botão)
- Relatório — filtro por banco/período/cód. master/cód. indicado, contagem
  e download em .xlsx (aberto a todos)
- Parâmetros — "Nova configuração" primeiro, em layout compacto (grade
  densa, vários campos por linha); validação **inline** (erro aparece
  embaixo do campo que falta, só ao tentar salvar — sem card de regras
  fixo); listagem somente leitura com botão de editar (abre modal central,
  não expande a tabela) e excluir
- **Coluna de tratamento "indicado"** na base consolidada — nunca vem do
  arquivo importado; é calculada automaticamente durante a importação
  (mesmo lookup do `cod_indicado`, mas devolvendo o nome). A tabela real
  ganha essa coluna sozinha (`ALTER TABLE`) na primeira vez que precisar.
  Linha sem indicado cadastrado ainda fica vazia, aguardando o futuro
  cruzamento de dados em Manutenção
- **Botões**: fundo escuro com borda roxa (sem preenchimento sólido, sem
  brilho/glow) — botão de excluir agora é um quadrado de verdade, sem
  distorção
- **Menu do usuário**: ícone escuro com borda roxa (sem gradiente colorido);
  nome/e-mail sempre exibidos exatamente como cadastrados, nunca forçados
  em caixa alta
- **Visão geral**: mudar banco/período só é aplicado ao clicar em
  "Atualizar agora" — trocar o filtro sozinho não dispara mais consulta
  nenhuma (Relatório já funcionava assim)
- **Manutenção → Map Indicado** (Admin): primeira função real de
  cruzamento de dados. Procura em `cod_corretor`, `cod_master` e
  `cod_indicado` por um código que bata com algum indicado cadastrado; se
  bater, grava esse código na nova coluna `map_indicado` (criada sozinha
  via `ALTER TABLE`, mesmo padrão da coluna "indicado"). Só processa
  linhas que ainda não têm `map_indicado` preenchido — rodar de novo
  depois de cadastrar indicados novos é seguro, não reprocessa nem
  sobrescreve o que já tinha sido mapeado. `Map Convênio` e `Map Produto`
  ficam para os próximos passos, seguindo o mesmo padrão
- **Modal de Critérios reorganizado** — formato de tabela compacta em vez do
  grid que quebrava mal; Prazo Mín./Máx. e Valor Mín./Máx. agrupados lado a
  lado; campo Tabela aceita vários códigos separados por `;` (cria um
  critério por código); "% especial" exibido formatado (`1,5` → `1,5%`)
- **Filtro de produção no Cadastro de Campanha** (opcional) — Map
  Indicado / Map Convênio / Map Produto, pra definir depois qual produção
  conta pro atingimento de meta de cada campanha. Sem seleção, considera
  toda a produção do banco no período. A rota que alimenta essas opções
  (`/api/manutencao/valores-mapeados`) já está pronta — Convênio/Produto
  ainda vêm vazios até esses cruzamentos existirem
- **Detalhamento por indicado** — "planilha dinâmica" na tela de
  Indicados: acordeão de 4 níveis (Banco → Indicado → Convênio → Produto),
  cada nível com subtotal, expansível/recolhível. Usa `map_convenio`/
  `map_produto` (colunas de tratamento) em vez das colunas brutas, e só
  mostra produção de **indicados já cadastrados** — código sem cadastro
  correspondente fica de fora, mesmo que já tenha passado pelo
  cruzamento em Manutenção. Filtro de banco/período (só busca ao clicar
  "Atualizar agora") + botão de download em `.xlsx` com os mesmos dados
- **Projeto renomeado para Aegis**
- **Nova identidade visual** — logo em escudo com gráfico/circuito (fornecida
  por você, recortada e com fundo transparente), cor de destaque trocada
  de roxo para índigo (aplicada centralizadamente via `theme.css`, sem
  nenhuma cor hardcoded no resto do app)
- Gráfico de produção diária: data no eixo em formato dd/mm, e nenhum
  elemento do Plotly (incluindo o tooltip de hover) mais usa cor padrão
  da biblioteca
- Textos explicativos repetitivos removidos (Dashboard, Campanhas Visão
  Geral, Indicados); campos obrigatórios agora têm uma dica sutil "Obrigatório"
  logo abaixo do campo, em vez de um parágrafo à parte
- Campanhas — Visão geral: colunas "Map Convênio" e "Map Produto" na
  tabela, mostrando os filtros de produção configurados em cada campanha
- Detalhamento por indicado: filtro de texto pra localizar um código de
  indicado específico (aplica na hora, sem precisar de "Atualizar agora")
- **Relatório de apuração por proposta** — botão de download no Cadastro
  de Campanha: gera um `.xlsx` com toda a produção do banco no período da
  campanha, linha por linha, com uma coluna "Valor apuração" calculada
  automaticamente — aplica o % especial do critério correspondente (se
  houver), zera quando o critério estiver marcado "Não contabilizar"
  (nova opção de status em Critérios), ou mantém o valor normal quando
  nenhum critério bate com aquela linha
- **Marca visual (logo)**: ícone maior na sidebar/login (52px/72px, era
  36px — herdado do tamanho antigo do monograma), letra "e" de "Aegis"
  destacada na cor de acento
- **Correção de performance (N+1 consultas)** — a Visão geral de
  Campanhas fazia uma consulta nova ao BigQuery pra CADA campanha, em
  sequência, sem cache nenhum (com 10 campanhas = 10 idas e voltas ao
  banco antes da página responder). Idem pros filtros de produção do
  Cadastro de Campanha (3 consultas a cada carregamento). Ambos agora têm
  cache — testei com 5 campanhas: a 2ª chamada não fez nenhuma consulta
  nova e ficou mais de 100x mais rápida. Também corrigi uma lacuna
  relacionada: rodar o cruzamento de dados em Manutenção não invalidava
  o cache — agora invalida corretamente
- Filtros de produção no cadastro de campanha viraram lista suspensa com
  seleção múltipla (`MultiSelectDropdown`) em vez do `<select multiple>`
  nativo do navegador; rótulos simplificados pra Indicado/Convênio/Produto
- Modais agora renderizam via portal (`components/Modal.jsx`), corrigindo
  um bug onde a animação de entrada das páginas (`.fade-in`/`.card`)
  deixava um `transform` residual que quebrava a centralização de
  qualquer modal aninhado nelas
- Números de tabelas/KPIs trocados de fonte monoespaçada (que sempre deixa
  algum traço/ponto no zero) para a fonte de texto normal com dígitos
  tabulares (`tabular-nums`) — alinha igual uma mono, mas sem nenhuma
  marca estranha no zero
- Setinhas de incrementar/decrementar removidas de todo campo numérico do
  app (preenchimento livre, sem risco de mudar o valor sem querer)
- Parâmetros agora abre em modal (botão "+ Nova configuração" no
  cabeçalho), com o formulário em formato de tabela — mesmo modelo usado
  em Critérios
- **Valor Base dos critérios** virou uma escolha entre Líquido
  (`vlr_liquido`) e Bruto (`vlr_bruto`) em vez de número digitado — cada
  critério escolhe o seu; se não escolher, herda o padrão da campanha
  (`base_producao`, também configurável no Cadastro de Campanha)
- **Módulo Valores em Aberto** — **DESATIVADO** (some do menu, das rotas,
  do botão em Campanhas → Cadastro, da Manutenção e do papel em Usuários;
  a API responde 404). Código e tabela `valores_abertos` no BigQuery
  continuam intactos. Para reativar: `VALORES_ABERTOS_ATIVO = true` em
  `frontend/src/config/funcionalidades.js` **e** variável de ambiente
  `VALORES_ABERTOS_ATIVO=true` no Railway. Como era (Editor/Admin) — grupo
  na sidebar com 3 telas:
  - *Visão geral*: KPIs (pendente total, previsto hoje, em atraso) +
    listas detalhadas, como um fluxo de caixa
  - *Cadastro*: lançamentos manuais (Banco, Categoria, Período de Ref,
    Valor, Data Prevista) — categorias: Nota Fiscal, Campanha, Bônus,
    Diferido, Colchão, Outro
  - *Acompanhamento*: todos os lançamentos, com auditoria completa (quem
    criou, quem marcou como recebido e quando) e as ações "Marcar
    recebido" / "Voltar para em aberto"
  - Botão "Adicionar aos valores em aberto" direto na listagem de
    Cadastro de Campanha (manual, pré-preenche banco/categoria/período a
    partir da campanha)
- **Login com primeiro acesso** — o admin agora cadastra só e-mail/nome/
  papel (senha vira opcional); a pessoa acessa a tela de login → "Primeiro
  acesso", informa o e-mail cadastrado e cria a própria senha. Tabela de
  usuários mostra o status ("Ativo" / "Aguardando primeiro acesso"). Nota
  de segurança: como não há e-mail de verificação, o fluxo confia que só
  a própria pessoa conhece o e-mail dela — razoável pra uma ferramenta
  interna onde só o admin cria contas, mas vale lembrar dessa limitação
- **Atingimento de meta de campanha** — a Visão geral de Campanhas agora
  liga cada campanha à produção real da base consolidada (respeitando
  líquido/bruto e os filtros de Map Indicado/Convênio/Produto). Mostra
  produção acumulada, % de atingimento, meta prevista, valor previsto,
  próxima meta/faixa — ou "Teto da campanha atingido" quando não há mais
  faixa a perseguir. Filtros de banco/período/campanha entre os cards e a
  tabela (só aplicam ao clicar "Atualizar agora")
- Logo própria (ícone de escudo) — favicon, sidebar e tela de login
- Indicados — CRUD da base usada como lookup na importação (Editor/Admin)
- Campanhas:
  - **Cadastro**: sem vigência, com faixas/metas dinâmicas (botão "+
    Adicionar faixa") e status (Vigente/Finalizada/Em Apuração, trocável
    direto na listagem — toda campanha nova nasce Vigente)
  - **Critérios**: reestruturado em Filtros (Banco/Campanha/Status) +
    Listagem de campanhas, com botão "Cadastrar critério" que abre um
    modal. Regra de negócio: não é possível criar/editar/excluir critério
    de campanha Finalizada ou Em Apuração
  - **Histórico de critérios**: página própria com o registro de cada
    alteração (criado/editado/excluído, quem, quando)
- **Manutenção** (Admin) — página reservada para a futura função de
  cruzamento de dados (ainda não implementada, só o espaço já existe)
- Sidebar com navegação client-side, filtrada por papel

## O que ainda falta portar

Visualizar e Explorar — só existem no projeto Flask+Dash anterior. O link
de Visualizar já está na sidebar mas ainda não tem rota no `App.jsx`.

## Testado

Testei exaustivamente o **backend** (login, sessão, permissões por papel,
CRUD completo de campanhas/critérios/usuários, todos os casos de erro)
via requisições HTTP simuladas. Testei a **integração** entre os dois
servidores de verdade (proxy do Vite, cookies de sessão cross-port, login
funcionando via `localhost:5173`) rodando os dois processos e fazendo
requisições reais. O **build do frontend compila sem erros**.

**O que eu não consigo testar sem um navegador de verdade:** o
comportamento visual/interativo do React em si (cliques, formulários,
re-render). Recomendo que, ao testar, você abra o DevTools do navegador
(aba Network) e confirme visualmente que trocar de página não dispara
uma nova chamada de API pra dados já carregados — é o comportamento que
essa arquitetura deveria garantir.

## Deploy no Railway (produção)

O Railway constrói a imagem pelo `Dockerfile` da raiz (build do React +
Flask/gunicorn servindo o build), com as opções de `railway.json`
(1 réplica, health check em `/`, reinício automático em falha). A porta
vem do Railway em `$PORT`; o `gunicorn` sobe com 1 processo, 4 threads e
timeout de 300 s (importação de arquivo grande).

### Passo a passo

1. No Railway: **New Project → Deploy from GitHub repo** →
   `Aegis_lake`, branch `main`. Ele detecta o `Dockerfile` sozinho.
2. Em **Variables** do serviço, cadastrar:
   - `BIGQUERY_PROJECT_ID`
   - `GOOGLE_CREDENTIALS_JSON` — o **conteúdo inteiro** do JSON da
     credencial de serviço do Google Cloud (o mesmo valor usado na Render)
   - `ADMIN_BOOTSTRAP_EMAIL` / `ADMIN_BOOTSTRAP_PASSWORD`
   - `FLASK_SECRET_KEY` — um texto aleatório longo e fixo. Sem ela, cada
     deploy gera uma chave nova e todo mundo é deslogado.
   - opcionais: `BIGQUERY_DATASET`, `BIGQUERY_TABLE`, `RESUMO_MESES`,
     `RESUMO_TTL_MIN`, `RESUMO_MAX_LINHAS`
3. Em **Settings → Networking → Generate Domain** para ter a URL pública
   (ou apontar um domínio próprio).
4. Todo push no `main` faz deploy automático.

O serviço não "dorme" como no plano free da Render, então o cache e o
resumo em memória ficam quentes entre um acesso e outro.

## Deploy no Render (antigo)

Mantido só durante a transição para o Railway — depois de desligar o
serviço na Render, este `render.yaml` pode ser removido.

O projeto roda como **um único serviço**: o Flask serve tanto a API
(`/api/*`) quanto os arquivos do React já compilados (todo o resto) — não
tem CORS nem cookie entre domínios diferentes pra configurar, porque
front e back ficam na mesma origem em produção.

### Passo a passo

1. Suba este projeto num repositório Git (GitHub/GitLab) — o Render
   precisa de um repositório pra conectar.
2. No painel do Render: **New → Blueprint**, aponte pro repositório. Ele
   vai detectar o `render.yaml` da raiz automaticamente e propor criar o
   serviço `aegis`.
3. Antes de confirmar, preencha as variáveis de ambiente marcadas como
   obrigatórias (o Render vai pedir na hora):
   - `BIGQUERY_PROJECT_ID` — ex: `db-tesouraria`
   - `GOOGLE_CREDENTIALS_JSON` — cole o **conteúdo inteiro** do JSON da
     credencial de serviço do Google Cloud (não use
     `GOOGLE_APPLICATION_CREDENTIALS` aqui — não tem como apontar pra um
     arquivo em disco num serviço do Render, então tem que ser o JSON
     colado direto)
   - `ADMIN_BOOTSTRAP_EMAIL` / `ADMIN_BOOTSTRAP_PASSWORD` — credencial do
     primeiro admin, criada automaticamente no primeiro acesso
   - `FLASK_SECRET_KEY` já vem gerada automaticamente pelo Render
     (`generateValue: true` no `render.yaml`) — não precisa preencher
4. Deploy. O Render roda o `buildCommand` (instala e compila o frontend,
   depois instala as dependências do backend) e sobe o `startCommand`
   (`gunicorn` servindo o Flask).

### O que esperar no plano gratuito

O plano free "dorme" depois de ~15 minutos sem uso. A primeira
requisição depois de um tempo parado acorda o serviço do zero — nesse
momento específico, o cache do servidor e a thread de aquecimento
começam vazios de novo (a mesma lentidão inicial que a arquitetura
resolve para o resto da sessão). Pra teste, isso é normal e esperado; num
uso contínuo de verdade, o plano pago mantém o serviço sempre ligado.

### Importação de arquivos grandes

O `gunicorn` (servidor de produção) mata qualquer requisição que passe do
tempo limite configurado — o padrão dele é **30 segundos**, e importar um
arquivo grande (ler o Excel, processar, gravar no BigQuery) facilmente
passa disso. Quando isso acontece, o navegador só vê uma falha genérica,
sem mensagem clara do motivo.

O `render.yaml` já vem configurado com um limite bem mais folgado
(`--timeout 300`, ou seja, 5 minutos) — confirmei isso rodando o mesmo
cenário localmente: com o timeout padrão a requisição falha exatamente no
segundo 2 (usei um teste artificial mais curto pra não esperar 30s de
verdade); com o timeout de 300s, a mesma requisição lenta completa
normalmente.

Se mesmo assim um arquivo específico continuar falhando, dois pontos a
considerar:
- **Excel é lento de ler** — a biblioteca que lemos `.xlsx` (`openpyxl`)
  é bem mais lenta que ler `.csv` pra arquivos grandes. Se a fonte do
  arquivo permitir exportar como `.csv`, tende a importar bem mais rápido.
- **RAM do plano gratuito** — o Render free tem só 512 MB de memória. Um
  arquivo realmente enorme pode estourar isso independente do tempo. Se
  isso acontecer, o sintoma é parecido (a importação simplesmente morre no
  meio), mas a solução é diferente: precisaria de um plano com mais
  memória, não só mais tempo.

### Rodando local sem Render

Nada muda no dia a dia — continue com os dois terminais (`python app.py`
+ `npm run dev`) descritos acima. A rota que serve o frontend compilado
só entra em ação se a pasta `frontend/dist` existir (ela não existe
localmente a menos que você rode `npm run build` manualmente), então não
interfere no fluxo de desenvolvimento normal.
