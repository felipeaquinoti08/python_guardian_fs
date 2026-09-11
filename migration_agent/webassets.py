"""Assets estáticos embutidos da UI local (logo do Guardian).

Embutido como base64 direto no source (em vez de um arquivo separado em
`dist\\..\\_internal` ou similar) de propósito: qualquer arquivo novo pra
empacotar precisa ser harvestado pelo heat.exe e reconhecido pelo WiX
(installer.wxs, issue #107 -- builds 23-29 desse mesmo issue mostraram
como cada mudança no instalador pode quebrar de formas imprevisíveis
sem um Windows/WiX real pra testar). Um base64 dentro do .py já viaja
junto com o resto do bytecode congelado pelo PyInstaller, sem tocar em
nada do empacotamento.

PNG gerado a partir de public/assets/images/logo/guardian_logo.png
(redimensionado pra 320x213, paleta de 64 cores) -- o mesmo logo usado
no login do Guardian (resources/views/auth/login.blade.php).
"""

from __future__ import annotations

import base64

GUARDIAN_LOGO_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAUAAAADVCAMAAAARktncAAAAjVBMVEX////u8fbE3fX09PfM3/Vws/IskvBsr++WxvRGmu0SivUY"
    "kfPY5vaz0/RTp/EJh/Y/RE1Cl+o+U2yTwO83l+4xjukVhuwIhO7X5fbo7ffw8ff29PiMvvEpMDoqMTwoLzkbJDEeJjAhKTMZIywi"
    "KjQhKDIqMjsFfPTr7/cJduY2PUcHdOEDd+kkg+YCct8H0jVRAAAAAXRSTlMAQObYZgAADFhJREFUeNrtnA17okoShZOJEMRBM3cS"
    "UTTSzUe4bEz+/8/b6upu6AaMmnvX3efuOc+MEkGUl1NdVQ3J3R0EQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAE"
    "QRAEQRAEQRAEQRAEQRAEQRAEQRAEQRAEQf97ur//cQ8K38f3MAvC8BEIv80vmseLOJ7Dhd/C9zNZLlbxYrGIF8v7JwC5Fh9F74oU"
    "Kw/GFMdw4XWDXzRfxCsjWojDAIF8eer9GYXKdyuXICGECy9zXxKEMQfvqlfMLlzChWfx/YiWoUHmAtQ/UzpBUfOFnu6jIFysfHAD"
    "hJRPfoHUpP74/TgOXZefCeV4ET6/ANdAL7+fbdaNR9S8F6gwVI/Pv/4ANcPu5Xm96G0Xn3KgXb2ItRGVE2FF0jqeCNXV6kuM3foY"
    "/FyAqU8uDsMlKQzjU0hjADzlQCr6omSzzUjbTUK1TTwd0OA3Aqi0WEabbOco20RLOPBSgPE82hK07SyJAlKUzPjHZIwQAKcAhtFG"
    "4QrmoU24i3mQEMPNPgTACYCpz2SZsNsWo6De7rJkDoBnAMbLzS6bDfFphEm22/hhDIAkn0iwybZROF36LaJstw1SADwNUPlvGyzS"
    "rm8LvfJlqQbCZXoZwNdDnucizw+v/6UDkyKXNwHoVM7zGfGzrUgYUCE4C1NnrSptNvP0PMAiF6Kkf/SfJG/HkM5ZmR/UEn94dVMH"
    "xmGSWX7xUuViwtUDpLU7IpglDtNpgK+5MCoZYSny4kYA+aTltFAJPnk3TSJxlGWRTh/hfquqZyoEF104J6otUVAj5y0TO6zzHp16"
    "0gvyRgDV50kOYP7YtxsCjOeb3SzszLblqf3UySAMMNvutt0wOOXAt1yZwGBzCIr61gDL23yoBagsZtCEMwpUXbBYVIuA++LdJlhu"
    "sqR7zxhgIQyx/tGgvAnBDmDNX0PeMITj5ZbIGP8NS5nY8JstYwp0x4Kj+C0tOlkwsFpKNqIyxK0Acuoo7MJ/HmBqR8AtJ1i1QMNc"
    "mnrlDc/LUP5IV6FrweHerOukY7daCu3C/JYAb9iJaBgdl4Cd6PGjAoa02S8MaSpl0kmAUg96ohi9zFWN/CcDDLJMdRnpIqHaxecX"
    "Mr+ZbeMo1vepmX0dDoAa4KhokYy1+oc6cLVqlK9UzUdcCE/A8ZvqXiROQy5gEus6lWOShXrPCKDUtXMx1RaU1TVJpH59vWLzuq7d"
    "OrA6u9n0R36v5lF5o9FUlBnTJFMG5AS8CKKfP6MgogGQskpvSmY9AbBmA06nPunW2UrOsRxyeTg4A2TBeYc6sZp7i6ridvAtP5i3"
    "FapFrJ2uR42w+eGNAQoDsJKycttIqZsjblPeKtrngWvWQ3XIaau64tW6ibm+E1EAVVxyithFbD9ajH4+kH7++We2CdZNqkRbpmlA"
    "ebhRP69Sb1eVTsDnPpGbhPLNC3CnaWAgZWmCvhY2+AtOUDWB5E9568omfdq4A6k7gLUqn8rc+2q6PCBeFW/Gp1xHTMF1Au/n9XsO"
    "TJeZjtzldhswqtV6/2D0Z7JMiRcxaxralKO8UYsDgCYBn+/yByWudLlXXDmqF/ho8x4gl0NFbXrsN/NWW6ur/3neh7BwR91+M7a2"
    "Lqs0QJXbqlKYcnWcAC8FGOyyJVFryF0hD4Hx/Aexu79XBB/ZeQ1LuZTcOgGw1gCLCwCWI4B8PJ2Lu0bGZPUOoMgtiDc365tjd7b2"
    "AHb7EbYmVZ+hy20HrCnB6muTCNsq2FEVSC7b0/jWcIZ4fGCA9xogozNPKszbdASw0N/y7hKAZTntwKLrovWzOTppKuOyC24+TJef"
    "bRstNifvy25eqBsczBetRU+we11+HyAtBUm0ZnOt9pqfQvhjGmC6asddnAfwX/lBjfwHejxw6jicAWjjUeWPWk4AtAerxqo3Q1nq"
    "rUsPYO9AaeypeqO6kPY8CONA3f0VlKLtquss+M4AG+1AWox5qKNgfjT4CODekNMx3DBAptyOnSX8fCHsiCa6DFPx4mQI6130HjBQ"
    "fICy8Ksm6X8BUwf2BUHpF1dd2JqOWfQfWJTf6KAtwEyNgWac41fm1n8PD/MBQBoDj/zCOYDWQ93U1hmAQgxiSI4BSn/MdctmQ9AH"
    "aAKjdvt1L4TdU+DivFAfK8alMuuxcUVZWON72K8NOfOkMrYG+DEKYSHGTJ0hRmqAYhpgPd6DE8Ji0M7IcdXpOtCClyNXSWcMFN4H"
    "1mV50TDu6mXFWKgV3rcewCbUdcxPqqHdl1OVqefNFEAxFcJTAE84UIpRxyeHAAcdtt82ygmA5Ti1lf4Zk+4acTVAHZ5r1Ym0x54S"
    "vRwvqZSOgnXqg00jalY0wJdRGeM3cpIGeBKBc8b3aYCiA1j782OlF4rSLTsVwbtxJdVlYd6a60o5bs3VO1+HAHW+uQ6ghtKq/uzo"
    "2EyHsbosdzwOYnuWJekEwLvSH9MHh8Ynt+i6gqksLEcWZhDWgWJ0sKNP8wGq88WF9xCgOA3wkl7KV7vSA5saBH1SzXHwrBdVs6Kq"
    "mMbPIV+3cnXZfbUpgLqzEmcBuhErxmS0gzyAPEM0qO4L67PX4bW7bzjw7oNRNDwf2A5wjfkddQTzhv4Q2GWR6cmEPoNO1YE6FuXE"
    "+e8uSRXD+P4aoB1OazHlwA7gYPZm6hReFMPESE3V20FwbqL5aKA1zWJu1xHpaDKC7eA81cwVzkSrBvg2MJnoABbDKUY3idQe2nIw"
    "BhblCODdRHch7WTCK7fA1TDpX9kNG1KhmolmSG2wTea+GcOEwpYHw7Wa+j9qgE8jTLqTfR0HcOm2a4NDKq0DCx4o5fBYTwCUYoRG"
    "ihMAve6iFh1A8XcAfDeQiAxBUoaL1OW3sAe4CDa7LGrZisvtLtFmTN9PtLmjKek3U8UUDmY5yCFdVeE5uO5neIph8h5VyKaxGLRy"
    "Y85OHTicKf9OEqEY1smDYpMSsRK5bLeb7efrdZqu1/P9LNttI3Kn8ulst7EGfLmbdppKhPVEPyL7hOxgqssOoClo+oHObGoB+vX3"
    "qG8xexoA1GNnWQ07prJrSv5iFiYLaoBHvpzUKIJtoO6D2W4S0kbdpDALWrYjoaWCWwNsJ3ZVdJMb9m6O16qbJ/GHStePtqAr/FHU"
    "7O0UwMoAK+78mZzhZIKp4fVb68pcee07EelfU7ke4MuRk0K7TjITqMdjyDelaqmbFI4cz/E+25mBsmlepuequukhNU2SdzNIjq8q"
    "+0olK2kZlW500aEXhTnSUojpEDZngvOolFV/G8QAYGFXSFnIfrr7bwRoLHhs1S0J+7VJveu5uj06SaJgvtbrm3Ww3c1sud1+Ndsn"
    "xGCSzuvmhTczZSh4Y4C5ucu9s2bsQDsK+jJ1lHDnA/uV5WA+cBjC3wP4YgAe5zN1+Uj/pB7btjVLjY5fPQDy+lO/pVT001d2ulIM"
    "kmXhHm9ptvQnm0Q/VX8a4ICgMFPZ+fD6XHUxQPmNOpCLaY7bVhFUd0IbYpxQGguzmVOEb+aG6PH9qyln4Rx/OXFJ3fFESXFVOk2t"
    "nUbV45T8F5+Lfkq/nmhwuosA8nUaoO51OjvX0k/7+V8rpJWeuHxRUjdmbfY6TI+O+5pwv2G2muuJEdCZV3OvMsgJm3ZzwfbwBi5g"
    "ZxY8F6BTqL6oVE9fYbGpxwHoNUV1/40qc4b/VoB3Lyp36BJG3Ro424e6bDYVThsGM0omUXi0vjz3W8O1yg/KQlUxPUNe0HopdWqs"
    "a/+ad8ETOFXhrxtu9fWehlvXUstfOdrq1IdcEsRGrbodX/+iCEGkUXCtbvbVpYzl91UA/7/qvSN4XHMVaMpAXQgqfKE1KWF9ArCp"
    "IO4R+r8sl21UJegIvyf85TDIHguX+ySZbTZUCe6XoUOXahvwO1UN+lLj33rdOvA+1XKLPztxQr/a40l9dgAb8DtNsGP1+emw+7Q/"
    "q8fmGZwu8OBn96TpqX+talXA7yzBz54gk/s0T/xK+whG5wh+cjL+NJGrxRzVDM1vEDqnPz5a13ufPcNm/Qx+l+h32zQmYh2C5MtH"
    "/LWiC0343GpkrgGPIex3xUj43sWvxnhsn/GH767Rkzbhp6kJ22dE79Um/LAAj8cP4PtWb/xBJQ0R/MDcwbcR/np/f3kCBwiCIAiC"
    "IAiCIAiCIAiCIAiCIAiCIAiCIAiCIAiCIAiCIAiCIAiCIAiCIAiCIAiCIAiCIAiCIAiCIAiCIAiCIAiCTuvfA9j/ywzZcbgAAAAA"
    "SUVORK5CYII="
)

GUARDIAN_LOGO_PNG = base64.b64decode(GUARDIAN_LOGO_PNG_BASE64)
