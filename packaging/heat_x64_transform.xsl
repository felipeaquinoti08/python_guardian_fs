<?xml version="1.0" encoding="UTF-8"?>
<!--
  Issue #107: heat.exe (harvester do WiX Toolset) gera <Component> em
  32-bit por padrao ao escanear um diretorio ("dir" harvest), mesmo que a
  flag "-platform x64" seja passada (nao tem efeito nesse modo de
  harvest), e o INTERNALDIR fica dentro de ProgramFiles64Folder, entao
  o light.exe rejeita com ICE80 ("32BitComponent uses 64BitDirectory").
  Aplicado via "heat.exe ... -t heat_x64_transform.xsl" (build-agent-msi.yml):
  adiciona Win64="yes" em todo <Component> gerado, sem tocar em mais nada.
-->
<xsl:stylesheet version="1.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:wix="http://schemas.microsoft.com/wix/2006/wi">
  <xsl:output method="xml" indent="yes" />

  <xsl:template match="@*|node()">
    <xsl:copy>
      <xsl:apply-templates select="@*|node()" />
    </xsl:copy>
  </xsl:template>

  <xsl:template match="wix:Component">
    <xsl:copy>
      <xsl:attribute name="Win64">yes</xsl:attribute>
      <xsl:apply-templates select="@*|node()" />
    </xsl:copy>
  </xsl:template>
</xsl:stylesheet>
