# ContentBot Pro Free

Turn a topic or your own script into a video with footage, voiceover and subtitles.
Use local files, YouTube and stock media — separately or in a mix.
Free, open-source Windows app. No activation or subscription to the app.

[Download for Windows](https://github.com/antishnaps/shorts-generator/releases/latest) · [Русская инструкция](docs/README.ru.md) · [GPL 3.0](LICENSE)

![ContentBot Pro workspace](docs/images/workspace.png)

## Start here

1. Download **ContentBotPro-Free-Windows-x64.zip**, extract the whole folder and open **ContentBotPro.exe**. Keep `_internal` beside it. Python is already included.
2. Sign in to [Google AI Studio](https://aistudio.google.com/apikey). Open **API Keys**, create a Gemini API key for your project and copy it. [Google's key guide](https://ai.google.dev/gemini-api/docs/api-key).
3. In the app, open **Settings → API keys**, click **Show** and paste the key into the **Google Gemini** field. Click **Test**, then **Save Settings**.
4. Open **Generation**. Enter a topic, choose the content language and start with one short video. For your own script, use **Custom text**.
5. In **Video → Smart video source mix**, enable **Add video clips to the timeline** and select your sources. Keep **Edge TTS** in **Audio** for a simple start, choose a subtitle style and click **Generate**.

For a first test, select only **Local videos** and choose your video folder.
This avoids stock API setup. Your own pictures can be selected in the visual
settings. Local media does not make online text generation or voiceover work offline.

Gemini has a [Free Tier](https://ai.google.dev/gemini-api/docs/billing) for eligible
models, with [usage limits](https://ai.google.dev/gemini-api/docs/rate-limits).
Check that your project is on **Free**, and do not enable **Set up billing** just
to follow this guide. Availability depends on the
[supported region](https://ai.google.dev/gemini-api/docs/available-regions).
The app is free; not every external API or AI feature is free.

## What you can make

- Videos from a topic, or from scripts you already wrote.
- Local video/image projects, YouTube footage, stock media or a combination.
- Voiceover, synchronized subtitles, fade/typewriter styles and configurable colors.
- Shot mixing, transitions, optional overlays and visual effects.
- Single videos, batches and series, with a queue and generation monitor.
- Optional publishing and analytics for your own authorized YouTube channels.

The interface supports Russian, English, Spanish, French, German, Chinese,
Japanese, Korean and Portuguese. Review the script, pronunciation and final video
before publishing.

## Choose your footage

| Source | Setup |
| --- | --- |
| Your videos | Select **Local videos** and a folder; no stock key needed. |
| Your pictures | Select an image folder in the visual settings. |
| YouTube | Enable **YouTube** in the source mix; downloads depend on source access. |
| Pexels | Add your **Pexels Video API** key in Settings, then enable Pexels. |
| Pixabay | Add your **Pixabay API** key in Settings, then enable Pixabay. |
| Wikimedia Commons | Enable the source; check each file's license and attribution. |

Choose one source or several. For local-only footage, turn off the online video
sources. Freesound is an optional **audio** source, not a video stock site.

## If something does not work

**Key test fails:** check the complete key, project access, quota and region in
AI Studio. **429** means a rate/quota limit; more keys in the same project do not
give independent quotas. **No footage:** check the folder, enabled sources and
their API keys; try a broader topic. Start with one video before running a large batch.

Keep keys, cookies and OAuth tokens private. Use media you have permission to
reuse. YouTube access does not grant republication rights; optional AI video,
image and avatar services may charge separately.

## Source and support

[Run from source and development](docs/DEVELOPMENT.md) · [Dependency notices](THIRD_PARTY_NOTICES.md) · [Portable build sources](SOURCE_ACCESS.md)

The **Support** button offers optional donations. The app stays free and never
initiates a payment. Receiving details are intentionally public; USDT/ETH use
**Ethereum**, not TRON or BNB Smart Chain. No donation is needed to use the app.
