/* O portão do painel — o quarto check do repositório.

   O `painel.js` tem 1819 linhas e é a superfície por onde o serviço é operado. O
   Python tem ruff, pyright estrito e 752 testes; isto aqui não tinha nada.

   As regras são de CORREÇÃO, não de estilo: o que se quer pegar é o `id` que sumiu do
   HTML, o global esquecido, o `==` onde devia ser `===`, o `await` dentro de laço que
   fecha sobre a variável errada. Formatação fica de fora de propósito — o arquivo já
   tem um estilo consistente e brigar com ele custaria diff sem ganho. */

export default [
  {
    files: ["flowpool/api/*.js"],
    languageOptions: {
      ecmaVersion: 2023,
      /* SCRIPT CLÁSSICO, E NÃO MÓDULO. O painel é servido direto pelo FastAPI, sem
         build step e sem Node em produção (ver o cabeçalho do painel.js). Marcar
         `module` aqui faria o eslint reclamar de cada função de topo — que é
         justamente o desenho. Por isso `no-implicit-globals` também fica fora. */
      sourceType: "script",
      globals: {
        document: "readonly", window: "readonly", location: "readonly",
        fetch: "readonly", console: "readonly", navigator: "readonly",
        setTimeout: "readonly", clearTimeout: "readonly",
        setInterval: "readonly", clearInterval: "readonly",
        localStorage: "readonly", requestAnimationFrame: "readonly",
        alert: "readonly", confirm: "readonly",
        URL: "readonly", URLSearchParams: "readonly", getComputedStyle: "readonly",
        FormData: "readonly", Blob: "readonly", File: "readonly",
        FileReader: "readonly", DataTransfer: "readonly",
        AbortController: "readonly", Image: "readonly",
        HTMLElement: "readonly", Event: "readonly", CustomEvent: "readonly",
      },
    },
    rules: {
      // o que quebra em produção
      "no-undef": "error",
      "no-unused-vars": "error",
      "no-unreachable": "error",
      "no-dupe-keys": "error",
      "no-duplicate-case": "error",
      "no-unsafe-negation": "error",
      "no-unsafe-optional-chaining": "error",
      "no-constant-condition": "error",
      "no-self-compare": "error",
      "no-unmodified-loop-condition": "error",
      "no-prototype-builtins": "error",
      "no-async-promise-executor": "error",
      "no-promise-executor-return": "error",
      "no-template-curly-in-string": "error",
      "array-callback-return": "error",
      "require-atomic-updates": "error",
      "no-loop-func": "error",
      "no-inner-declarations": "error",
      "no-fallthrough": "error",
      "default-case-last": "error",
      "no-useless-escape": "error",
      // `catch {}` vazio é permitido: os seis do painel têm o motivo escrito ao lado
      "no-empty": ["error", { allowEmptyCatch: true }],
      // o que evita a próxima classe de defeito
      eqeqeq: "error",
      "no-var": "error",
      "prefer-const": "error",
      "no-shadow": "error",
      "no-return-await": "error",
    },
  },
];
