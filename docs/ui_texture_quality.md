# Nitidez da interface e filtragem de materiais no BOTW

## Decisão

Adotar o Mac Native Games (MNG) como referência inicial para dois trabalhos futuros no SWX. O primeiro é aumentar a definição de assets selecionados da interface com um pack local de texturas. O segundo é aplicar filtragem anisotrópica seletiva aos materiais 3D. Cada recurso terá validação própria e poderá ser usado em 1080p, 1440p ou 4K. Este documento registra a escolha técnica. A implementação fica para uma etapa posterior.

## Interface em alta resolução

O **Sharp UI texture pack** do MNG suporta BOTW 1.9.0 e 1.6.0 no backend Metal. A ferramenta de captura coleta texturas usadas pela interface do jogo local. O gerador offline seleciona assets por uma lista de chaves, traça e rasteriza novamente as máscaras e produz PNGs de 2×, 4× ou 8× em cada eixo. A altura efetiva da imagem do jogo, como 1080, 1440 ou 2160, orienta a pré-filtragem. Assets que falham nas verificações do gerador usam a textura original. O runtime também pode aceitar imagens coloridas compatíveis. O gerador padrão se concentra em máscaras e pula arte colorida, texturas planas e gradientes puros. [Documentação do pack](../refs/mac-nativegames/docs/mods.md#sharp-ui-texture-pack-breath-of-the-wild-190-and-160-tears-of-the-kingdom-143) · [Gerador offline](../refs/mac-nativegames/tools/ui_pack/build.py)

No runtime, a chave combina hash dos bytes do guest, largura, altura e formato NVN. O MNG decodifica PNGs em uma fila de fundo e publica a `MTLTexture` no caminho de renderização quando o binding é revalidado. A substituição vale só para amostragem em views compatíveis da textura inteira. A textura original continua disponível para cópias, readback e renderização. Como os shaders usam UVs normalizados, o ícone mantém sua posição e tamanho na tela. O formato de amostragem, a interpretação sRGB e o swizzle precisam ser preservados. Um asset ausente ou incompatível segue pelo binding original. [Hook Metal e regras de elegibilidade](../refs/mac-nativegames/games/totk/third_party/d2r_gfx/gfx/nvn/ui_pack.h)

Para o SWX, a primeira implementação deve seguir essa separação entre geração offline e substituição no binding Metal. O protótipo pode começar por um conjunto pequeno de máscaras, ícones e molduras conhecidos do BOTW 1.9.0. A avaliação deve comparar a mesma tela com e sem pack, observar HUD, menus e ícones pequenos, verificar o fallback e medir o custo no primeiro carregamento. A seleção de texturas e o orçamento de memória precisam considerar os hitches de upload já observados no projeto.

## Filtragem anisotrópica de materiais

O MNG oferece anisotropia de 2× a 16× via `TOTK_ANISO` e pelo painel gráfico compartilhado pelos jogos. O backend aplica o ajuste no sampler de materiais elegíveis, como solo, paredes e folhagem vistos em ângulo. Os samplers de comparação de profundidade, amostragem nearest e caminhos fora dessa classe conservam o comportamento do jogo. O ajuste aumenta a definição em superfícies oblíquas usando os mipmaps existentes. [Configuração no MNG](../refs/mac-nativegames/docs/controls.md) · [Seleção dos samplers Metal](../refs/mac-nativegames/games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m)

No SWX, o ponto de entrada para a futura implementação é a criação e seleção de samplers Metal no NVN. A validação deve usar a mesma câmera e resolução, comparar chão ou caminhos ao longe com o ajuste ligado e desligado e verificar sombras, transparências e UI. Mip bias, sharpening e upscale da cena são controles distintos e ficam fora desse trabalho. [Backend Metal do SWX](../core/nxnative/dm/gfx/nvn/nvn_mtl.m)

## Referência complementar

O Savage Wind amplia fontes e texturas monocromáticas de menu em 2× ou 4× durante o upload no Vulkan e gera mipmaps para amostragem. Esse caminho é útil como referência para folhas de fonte e texturas exibidas em várias escalas. O MNG é o ponto de partida do SWX por já exercitar a substituição em Metal para o mesmo build do BOTW, com geração fora do jogo e preservação da textura original. A resolução nativa e o MetalFX de apresentação continuam tratando a imagem renderizada. [Ampliação no Savage Wind](../refs/savage-wind/runtime/nvn/text_upscale.c) · [Upload Vulkan e mipmaps](../refs/savage-wind/runtime/nvn/vulkan/textures.c)
