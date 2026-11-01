# Engine fonts

Fonts used by the renderers. Every file here is tracked in git. Caption burn-in reads this folder as the libass
`fontsdir`; HyperFrames builds copy only the files their scene uses (`hyperframes.HF_FONTS` always, `THEME_FONTS` by
theme role).

| File | Family | Used for | Licence | Source |
|---|---|---|---|---|
| `Inter-Bold.otf`, `Inter-SemiBold.otf` | Inter | Captions, drawn overlays, HyperFrames labels and cards | SIL Open Font License 1.1 | Inter project |
| `KaushanScript-Regular.ttf` | Kaushan Script | Theme `label_font = "script"`: teardrop pin labels | SIL Open Font License 1.1 (`KaushanScript-OFL.txt`) | google/fonts `ofl/kaushanscript` at commit 9862abc066c9 |
| `ArchivoBlack-Regular.ttf` | Archivo Black | Theme `title_font = "title"`: big titles and numbers | SIL Open Font License 1.1 (`ArchivoBlack-OFL.txt`) | google/fonts `ofl/archivoblack` at commit 9862abc066c9 |
| `PlayfairDisplay[wght].ttf` | Playfair Display (variable, weight 400-900; years use 800, profile [print] 450 on paper and 700 on dark imagery) | Year figures | SIL Open Font License 1.1 (`PlayfairDisplay-OFL.txt`) | google/fonts `ofl/playfairdisplay` at commit 9862abc066c9 |
| `EBGaramond[wght].ttf`, `EBGaramond-Italic[wght].ttf` | EB Garamond (variable, weight 400-800) | Profile [print] on paper: entity titles and place names (600 caps), pin labels (Italic 600) | SIL Open Font License 1.1 (`EBGaramond-OFL.txt`) | google/fonts `ofl/ebgaramond` at commit 9862abc066c9 |
| `Cinzel[wght].ttf` | Cinzel (variable, weight 400-900) | Profile [print]: date headings and the time under a year (600) | SIL Open Font License 1.1 (`Cinzel-OFL.txt`) | google/fonts `ofl/cinzel` at commit 9862abc066c9 |
| `Kalam-Bold.ttf` | Kalam | Profile [print]: hand annotations | SIL Open Font License 1.1 (`Kalam-OFL.txt`) | google/fonts `ofl/kalam` at commit 9862abc066c9 |

Adding a font: copy only the needed files and the family's licence file (`<Family>-OFL.txt` or `<Family>-LICENSE.txt`)
from a licence-clean source (OFL or Apache 2.0), add a row above, and register the role in `hyperframes.THEME_FONTS`.
