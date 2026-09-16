# motionfetch

Convierte **videos e imágenes en animaciones de terminal** — y córrelas junto a
[fastfetch](https://github.com/fastfetch-cli/fastfetch), girando en el sitio
como la dona clásica, hasta que pulses una tecla.

![dona girando junto a fastfetch](screenshots/fetch-donut.gif)

fastfetch no puede animar: imprime una vez y termina. motionfetch compone las
dos mitades él mismo — tu animación a la izquierda, `fastfetch --logo none` a
la derecha — y redibuja el bloque en el sitio. Cualquier tecla (o Ctrl+C) lo
detiene.

## Características

- **Convierte lo que sea**: videos mp4/mkv/webm/mov, GIFs o imágenes fijas.
- **Tres estilos**: `blocks` a todo color, rampa `ascii` clásica, o `braille`
  de alto detalle — ascii y braille son texto plano que se tiñe con el color
  de acento de tu tema al reproducirse.
- **Generadores incluidos**: `donut`, `matrix` (lluvia digital), `cube` — sin
  necesidad de video.
- **Recorte integrado**: `--crop-top 15%` elimina un texto o marca de agua sin
  abrir un editor de video.
- **Una biblioteca, no un one-off**: las animaciones se guardan por nombre;
  listar, reproducir, renombrar, borrar.
- **Un comando por animación**: `motionfetch link donut fastfetch_1` instala
  un comando `fastfetch_1` — crea los que quieras y cambia entre ellos.
- **Exporta previews**: renderiza cualquier animación a `.gif` o `.png` (así
  se hicieron todas las imágenes de este README).

| generador `matrix` | video → `blocks` | video → `braille` |
| --- | --- | --- |
| ![matrix](screenshots/matrix.gif) | ![blocks](screenshots/video-blocks.gif) | ![braille](screenshots/video-braille.gif) |

## Instalación

Necesita Python ≥ 3.9 y, para convertir video, `ffmpeg`.

```bash
pipx install git+https://github.com/0980491/motionfetch
```

En Arch: primero `sudo pacman -S --needed ffmpeg python-pipx`.

## Inicio rápido

```bash
motionfetch generate donut        # crea el toroide clásico
motionfetch fetch donut           # gíralo junto a fastfetch — cualquier tecla lo para
motionfetch add video-chevere.mp4 # convierte un video (muestra preview y pregunta)
motionfetch                       # navegador interactivo de tu biblioteca
```

## Convertir videos e imágenes

```bash
motionfetch add clip.mp4 --name lluvia --width 48 --fps 15
motionfetch add clip.mp4 --style ascii --gamma 0.6      # look de texto tintable
motionfetch add foto.png --style braille --invert
motionfetch add clip.mp4 --start 12 --duration 6        # solo esa sección
```

`add` muestra un preview en vivo y pregunta antes de guardar; `--no-preview`
se lo salta.

### Recorte

Si el video trae un texto, marca de agua o franjas negras que no quieres,
recórtalo al convertir — cada flag acepta píxeles o porcentaje:

```bash
motionfetch add clip.mp4 --crop-top 15% --crop-bottom 40
motionfetch add clip.mp4 --crop-left 10% --crop-right 10%
```

### Estilos

| estilo | color | ideal para |
| --- | --- | --- |
| `blocks` | truecolor, 2 píxeles por celda | video, fotos (por defecto) |
| `ascii` | mono, tintado al reproducir | logos, integración con tu tema |
| `ascii-color` | caracteres truecolor | fuentes coloridas con look retro |
| `braille` | mono, tintado al reproducir | line art, alto detalle |

Las animaciones mono toman su tinte de `$MOTIONFETCH_TINT`, luego de
`~/.config/quickshell/colors.json` (setups con matugen), y si no, un azul por
defecto — o pásalo explícito con `--tint '#a6e3a1'`.

## Reproducir

```bash
motionfetch play matrix               # bucle a pantalla completa
motionfetch fetch matrix              # junto a fastfetch
motionfetch fetch matrix --cmd nitch  # junto a cualquier otro fetch
motionfetch fetch matrix --secs 5     # se detiene solo a los 5 s
motionfetch fetch matrix --once       # un solo frame estático (para scripts)
```

Si la terminal es muy angosta para la pareja, motionfetch imprime solo la caja
de info en vez de dejar que la animación se rompa.

## Varias animaciones, varios comandos

```bash
motionfetch link donut                # instala `fastfetch-donut`
motionfetch link matrix fastfetch_1   # o ponle el nombre que quieras
motionfetch link cube --play          # comando que la reproduce sola
motionfetch unlink fastfetch_1
```

Los links son scripts diminutos en `~/.local/bin`, marcados para que `unlink`
se niegue a tocar algo que no creó. `motionfetch list` muestra a qué animación
apunta cada comando. Para que `fastfetch` a secas quede animado, agrega un
alias a tu shell:

```bash
alias fastfetch='motionfetch fetch donut'
```

## Exportar previews

```bash
motionfetch export donut donut.gif
motionfetch export donut still.png
motionfetch export donut hero.gif --fetch    # compuesta junto a fastfetch
```

## Licencia

MIT
