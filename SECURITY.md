---
type: documentation
status: current
created: 2026-09-15
updated: 2026-09-16
tags: [get-brolls]
---

# Segurança e privacidade

Não comite `.env`, originais, manifestos privados ou exports de revisão de clientes. Revise a árvore antes de publicar. O servidor de exemplo escuta apenas localhost. Não distribua `.venv/`, `.tools/`, perfis de navegador, cookies ou pares CDN assinados. Os instaladores obtêm as dependências nos registros oficiais.

Projetos/JSON são dados locais confiáveis. Não execute a skill como serviço público aceitando URLs, caminhos ou manifests arbitrários. O transporte HTTP interno de APIs/downloads resolve e valida todos os IPs antes da conexão, conecta aos IPs validados sem uma segunda resolução, mantém certificado/hostname HTTPS e bloqueia redirects. Ele não usa proxies automáticos do ambiente/sistema. Essa proteção não é um isolamento de rede de processos externos como yt-dlp, FFmpeg ou navegador.

A revisão não autentica quem clicou: importação exige atribuição humana com `--by`. O importador confere assinatura do conteúdo e versão da decisão (`reviewEpoch`) para impedir que exports antigos substituam decisões posteriores. Arquivos sem versão da decisão devem ser regenerados pelo Storyboard; não edite o JSON para contornar uma recusa.

Para reportar vulnerabilidades, use o canal privado de segurança do repositório quando habilitado pelo mantenedor. Até existir um canal privado, não publique segredos ou dados de clientes em issues. Nenhum endereço de contato é presumido neste pacote.
