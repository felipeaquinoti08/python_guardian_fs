"""Versão do agent, sobrescrita em build time pelo CI (issue #111, ver
.github/workflows/build-agent-msi.yml) com o mesmo valor gravado no
ProductVersion do .msi (installer.wxs). Usada pela própria UI local pra
comparar contra a versão mais recente publicada no Guardian (self-update).

Fora de um build real (checkout local, testes, dev) fica no placeholder
abaixo -- nunca deve aparecer instalado de verdade num cliente.
"""

AGENT_VERSION = "0.0.0-dev"
