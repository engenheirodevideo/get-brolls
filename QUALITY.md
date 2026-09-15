---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-15
tags: [get-brolls, quality, qa, evidence]
---

# Qualidade e evidências — Get B-rolls 2.3.4

Este documento reúne o estado de qualidade, as regressões cobertas, os limites conhecidos e as evidências reais por provedor. Resultados ao vivo são registros datados, não promessa de disponibilidade futura nem aprovação editorial.

## QA da versão 2.3.4

### Estado atual

**Validação desta entrega consolidada: 53 testes passaram localmente em 15/09/2026.** A seleção do pacote também impede a volta da pasta antiga de referências, de rótulos internos e de recursos ausentes no template do storyboard.

O fluxo próprio de Bruno foi reintegrado à pasta da skill. A afirmação anterior de que a 2.3.3 preservava o produto estava errada e foi retirada. O pacote de distribuição só deve ser gerado depois que a suíte, os links e a seleção de arquivos passarem.

**Validação histórica: 52 testes passaram na fonte e os mesmos 52 passaram na cópia limpa instalada, em macOS/Python 3.14.6 com FFmpeg.** Instalação numa cópia limpa da pasta, sem qualquer instalação do produto de origem nela: yt-dlp/EJS pelo PyPI e Playwright CLI 0.1.20 pelo npm passaram. Nenhuma biblioteca externa faz parte do código distribuído; o [guia](GUIDE.md#instalação) orienta a instalação na máquina de cada pessoa.

### Evidência por capacidade

| Capacidade | Evidência | Limite |
|---|---|---|
| YouTube sem API key | Busca real, aquisição 0–3 s e GIF; nova instalação limpa repetiu trecho 3–5 s em 1920×1080/25fps | Um vídeo público; aprovação permaneceu pendente |
| Instagram pares | Captura no Chrome, curl dos dois canais, merge e decodificação integral reais; MP4 50,226 s, 1076×1912, H.264/AAC | Um Reel público; identidade dos canais conferida por asset e prévia |
| Instagram Chrome logado | Nova tentativa concluiu captura/CDN/merge e GIF de 5 s | Sessão do próprio usuário; sem copiar cookies para a skill |
| TikTok | Vídeo @nasa/7665358680627399966 baixado via instalação limpa; trecho 3 s, 576×1024/30fps, H.264/AAC; decodificação e GIF passaram | Link anterior indisponível no navegador; houve bloqueios/erros em tentativas anteriores |
| Pexels/Pixabay | Busca, refresh, download integral, GIF e corte técnico reais com chaves temporárias | Ensaio dos adapters na rodada anterior; sem aprovação editorial fictícia |
| Commons/NASA | Busca, refresh e HEAD de mídia reais na rodada anterior | Não equivalem a download integral de todos os itens |
| Prévia → coleta final | Fixture real FFmpeg, cache/hash/offset, aprovação sintética e corte verificado | Simulação explícita, não aprovação atribuída ao usuário |
| Instalação | venv + import yt-dlp/EJS; npm + Playwright --version; --check valida Node 22+ | Executáveis do sistema conforme GUIDE; não instala extensões/login |

### Regressões cobertas

- YouTube e fontes sociais têm transporte, sem exigir chave YouTube.
- URL Instagram pode conter perfil antes de `/reel/`.
- Preview remoto mantém origem, autor, aprovação pendente e intervalo original.
- Cache é reutilizado por hash e o fetch desconta `local_start_s`, preservando o trecho visto.
- Helpers próprios encontram yt-dlp local e habilitam Node; coletor não reutiliza parcial de download fracassado.
- Node antigo é rejeitado antes de instalar dependências.
- Arquivos de bibliotecas, fontes externas, mídia e credenciais não estão na seleção de distribuição.
- Permanecem testes de recuperação do ledger, revisão obsoleta, rejeição, interrupção, proporção, GIF, regras e logs sem segredos.

### Verificação documental

Revisor independente conferiu os transportes e apontou problemas de runtime, parcial curl, instalação e reuso de sessão. Corrigidos com regressões e documentação. O guia Instagram agora diferencia plugin do agente e extensão Playwright, mostra attach/tab-list/tab-select e captura por requests/response-body ou CDP. Não existe promessa de parser automático de captura: como no processo original, o agente opera o navegador e entrega pares ao coletor.

### Limites preservados

YouTube, Instagram e TikTok têm agora pelo menos um ensaio real concluído nesta rodada. Isso não garante qualquer URL/sessão: o primeiro link TikTok estava indisponível no site e o extrator retornou bloqueio em tentativas anteriores. Reteste com URL disponível pelo navegador e com as dependências instaladas pelo [guia](GUIDE.md#instalação). Não confundir esses erros com ausência de implementação.

macOS/Linux usam Bash e fcntl; Windows nativo não validado. Descoberta automática em novas sessões Codex/Claude e CI remoto não foram executados nesta rodada. O template visual do Storyboard foi incorporado à entrega e precisa permanecer portátil, sem depender de mídia privada do caso usado no desenvolvimento. A revisão usa fontes do sistema; sem fontes baixadas ou bibliotecas embutidas. Aprovação editorial continua humana.

Comandos para repetir na pasta da skill:

```sh
bash scripts/install.sh --check
bash scripts/install.sh
python3 -m unittest discover -s tests -v
python3 scripts/gb.py doctor
```

`doctor --live` executa buscas limitadas reais e pode consumir quota dos bancos configurados; não testa toda a captura Instagram/TikTok. Os detalhes de origem aparecem na seção [Rotas e evidências](#rotas-e-evidências--15092026).

### Revisão final de documentação — 2.3.4

README, GUIDE, AGENTS, CHANGELOG, CONTRIBUTING, SECURITY e guias de navegação/revisão conferidos contra a implementação. AGENTS.md integra a seleção dos arquivos necessários à skill; `agents/openai.yaml` mantém a invocação `$get-brolls`.

Validação: 28 documentos com frontmatter e links relativos válidos; 13 exemplos de comandos principais aceitos pelo parser; scripts Bash com sintaxe válida. Revisão independente identificou e corrigiu dois pontos no README: `--project` obrigatório no exemplo local e distinção entre duração excedida (erro) e GIF acima do limite de bytes (fallback estático). Nenhum ZIP gerado/alterado. Essa revisão documental não altera a versão 2.3.4 nem substitui os ensaios ao vivo já registrados.

## Rotas e evidências — 15/09/2026

YouTube usa yt-dlp, sem YouTube Data API e sem API key. Pexels/Pixabay usam suas próprias APIs; Instagram usa o navegador e o coletor de dois canais; TikTok usa URL completa e yt-dlp.

| Fonte | Ensaio real | Resultado |
|---|---|---|
| YouTube | Busca NASA Artemis sem variável de chave; preview 0–3 s e repetição 3–5 s com yt-dlp instalado em pasta limpa | Busca/download/decodificação/GIF passaram, 1920×1080, 25fps; pendente de revisão |
| Instagram | Reel DcMXl1IPNtB; Chrome → dois streams → curl → coletor/FFmpeg | Download e merge completos: 50,226009 s, 1076×1912, H.264/AAC, 16.528.141 bytes; decodificação integral e GIF 5 s passaram |
| TikTok | @nasa/7665358680627399966 descoberto no navegador, via yt-dlp instalado pelo GUIDE | Trecho 0–3 s: 576×1024/30fps, H.264/AAC, 269.139 bytes; decodificação e GIF passaram |
| Pexels | coffee, ID 31264406; busca/refresh/download | 1080×1920, 12 s, 1.682.522 bytes |
| Pixabay | coffee, ID 46989; busca/refresh/download | 1920×1080, 35 s, 12.258.063 bytes |
| Commons | earth, 1 resultado; refresh e HEAD | 200 video/webm; corpo não baixado nesse ensaio |
| NASA API | busca, refresh e HEAD | 200 video/mp4; corpo não baixado nesse ensaio |

### Procedência

- [YouTube — lançamento Artemis](https://www.youtube.com/watch?v=B_7EUmCxcvE): metadados públicos e trecho técnico; não houve aprovação editorial do agente.
- [Instagram — NASA Johnson](https://www.instagram.com/nasajohnson/reel/DcMXl1IPNtB/): Captura e download dos dois canais concluídos no Chrome logado, com o coletor desta pasta.
- [TikTok — NASA, Earthset](https://www.tiktok.com/@nasa/video/7665358680627399966): busca no navegador e aquisição do trecho passaram. A URL anterior @nasa_space9/7512513421288492334 aparece indisponível no próprio navegador; seu erro não certificava o estado de toda a plataforma.
- [Pexels — espresso, İsa Kılavuzoğlu](https://www.pexels.com/video/close-up-of-espresso-brewing-into-elegant-cup-31264406/).
- [Pixabay — 46989, NickyPe](https://pixabay.com/videos/id-46989/).

Pexels/Pixabay geraram GIFs de 5 s (1.030.433 / 720.171 bytes) e cortes técnicos de 2 s verificados com ffprobe e FFmpeg. As chaves fornecidas foram usadas só no processo; não integram código, documentação, configs distribuídos ou exemplos. Esses ensaios validam os adapters; não representam aprovação/licença inventada nem garantia de disponibilidade futura.

O coletor Instagram passou também ensaio de captura/CDN real nesta nova tentativa. Fixtures continuam cobrindo detecção de áudio duplicado e falha de transferência. Na captura real, o asset_id e a duração relacionaram os dois canais; seletores de faixa bytestart/byteend foram removidos sem alterar a assinatura. Configs privados não integram a skill.

### Dependências oficiais

[yt-dlp/EJS](https://github.com/yt-dlp/yt-dlp/wiki/EJS) exige runtime suportado; o setup completo usa Node 22+ e instala yt-dlp com extras `default`. [Playwright CLI](https://github.com/microsoft/playwright-cli) é instalada via npm, separadamente do código da skill. [Pexels](https://www.pexels.com/api/documentation/) e [Pixabay](https://pixabay.com/api/docs/) documentam suas APIs; documentação não substitui teste autenticado.

A correção NASA promove HTTP somente no host exato images-assets.nasa.gov antes da validação. `doctor --live` busca nas fontes disponíveis e faz refresh de bancos; sem --live, inspeciona o ambiente local.
