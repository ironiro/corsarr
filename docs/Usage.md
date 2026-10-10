# Usage

In the group the bot only reacts when it is **mentioned** (`@yourbot …`) or when someone **replies** to one
of its messages. It ignores all other messages.

![A suggestion card, feedback after the movie and download notifications](images/telegram-chat.jpg)

## Suggestions

| Message | What happens |
| --- | --- |
| `@bot find a movie for tonight, thriller or horror` | 5–6 titles in the requested genre |
| `@bot I want a dumb action movie for tomorrow night` | the mood is taken into account too |
| `@bot what fits what we watched lately?` | based on your history, favourites (♥) and ratings |
| `@bot recommend series based on the series we watched` | series only, referring to the ones you watched |
| `@bot look for something new, some sci-fi` | only titles that are not in the library yet |

German works just as well (`@bot such einen Thriller für heute Abend`) – the bot answers in the language you write in (English or German).

### The suggestion card

Suggestions arrive as **one message to browse through** (plus a short intro):

- The first **1–2 titles** come from your **library** (watchable right away), the rest are **new** and can be
  requested. If you ask for something new, all titles are new.
- Every page shows poster, rating, runtime, plot and one line why the title fits.
- If you chose your streaming services ([`STREAMING_PROVIDERS`](Configuration.md#jellyseerr--seerr)), a new
  title that one of them includes shows it below the availability line – "📺 On Netflix, Disney+" – so you can
  watch it there instead of requesting a download. Only services in your subscription count (not rent or buy);
  the data comes from TMDB via Seerr. The Request button stays – you decide.
- **◀️ 2 / 6 ▶️** flips between titles – the message is updated in place.

| Button | Effect |
| --- | --- |
| ✅ **Let's watch** (library) | marks the title as picked; it won't be suggested for 14 days |
| 📥 **Request** (new) | requests the title in Jellyseerr; Sonarr/Radarr then download it |
| 🙅 **Not interested** | the title never comes back |
| 🎬 **Trailer** | opens the trailer on YouTube (if Jellyfin or TMDB knows one) |

Once someone decides, it is shown on that title's page ("✅ Picked by Sam – enjoy!"); browsing and the trailer link keep working. Several people
can browse and decide at the same time.

Never suggested: anything you've watched, started, rejected, rated or recently picked. Titles suggested in
the last 3 days move to the back.

## Asking about a specific title

Ask about one title, or describe it, and Corsarr looks it up instead of suggesting something:

- `@bot there's a new Marvel series with Vision now, isn't there?`
- `@bot get us Dune Part Two`
- `@bot there was this film where the guy only realises at the end that he was dead all along – what's it called?`

The language model turns the message into search terms (its best guess of the title, plus keywords), Corsarr
searches TMDB through Jellyseerr/Seerr, and the model picks the hit that is meant – usually one, at most three.
You get the same card as for suggestions: *In your library*, *Already requested – downloading*, or new with the
📥 **Request** button and the trailer. If nothing fits, the bot asks you to describe it differently or give the
original title.

Recognising a film from a plot description depends on the model's knowledge – Claude does this well, small local
models often don't.

## Feedback after watching

Requires the [Jellyfin webhook](Webhooks.md#jellyfin--feedback-after-watching).

| Situation | When | Question |
| --- | --- | --- |
| Movie stopped after about 80 % or marked as watched | right away | 👍 / 😐 / 👎 |
| Last episode of a season watched | right away | 👍 / 😐 / 👎 |
| Series not continued for a while (default 14 days) | at the next check (every 10 min) | 👍 / 😐 / 👎 |
| Movie stopped before 80 % and not resumed (default 3 days) | then | 👎 wasn't good / 😴 too tired / ▶️ we'll keep watching |
| Abandoned-movie question unanswered for 2 days | – | counts as a light 👎 |

- Every question starts with a header line showing which title it is about, e.g.
  **📺 Andor (2022) · season 1**.
- For details **reply directly to the question**, e.g. `too gory, but a good story`. The bot turns that into
  traits like *less: gore* and matches them with TMDB keywords – future suggestions with lots of gore move
  to the back.
- Playback of less than 5 minutes doesn't count. Unanswered questions expire after 7 days.
- The bot detects the end of a season itself: whenever playback of an episode stops, it checks Jellyfin to see whether all
  episodes of the season have been watched.

## Settings in the chat

| Message | Effect |
| --- | --- |
| `@bot ask about series only after 3 weeks` | series pause set to 21 days |
| `@bot ask about abandoned movies after 5 days` | follow-up for abandoned movies set to 5 days |
| `@bot no more pirate` · `@bot switch grandma on` · `@bot only the cat` | characters off/on |

The same settings are in the web interface under *Configuration → Bot behaviour*.

## Characters and language

- Twelve characters, each switched on or off on its own (web interface → Configuration → *Characters*, or
  in the chat: `@bot switch grandma on`, `@bot only the cat`, `@bot no more pirate`). Pirate and Gen Z are on
  by default. Each message is spoken by one active character; now and then two of them have a short exchange.
  With all off the bot writes plainly.

| Character | Emoji | Sounds like |
| --- | --- | --- |
| Pirate | 🏴‍☠️ | an old sea dog with dry humour |
| Gen Z | 📱 | a dry, self-deprecating mid-twenties group chat member |
| Butler | 🎩 | formal, British understatement |
| Film critic | 🧐 | well-read, a little snobbish, talks direction and camera |
| Video store clerk | 📼 | 90s nostalgia, "kept it behind the counter for you" |
| Film noir detective | 🕵️ | short hard sentences, rain and neon |
| Trailer voice | 🎙️ | "In a world …" |
| Ship's computer | 🤖 | analyses and probabilities, eerily polite |
| Grandma | 👵 | warm, worried, "don't stay up too late" |
| Sports commentator | ⚽ | covers the pick like a live match |
| Cat | 🐈 | bored, condescending, wants to sleep |
| Bard | 🧙 | tells it like a heroic saga |

- The characters avoid repeating themselves: the bot remembers its latest phrasings.
- **Language:** the bot answers in whatever language you write in – French, Spanish, Turkish, … – text,
  suggestion card, buttons, reasons and TMDB plot summaries (where TMDB has them in that language).
  German and English texts are built in. For any other language the model writes its replies in it directly,
  and the fixed texts (buttons, card lines, download messages) are translated **once** by the model and stored –
  about a third of a cent with Claude Haiku. A translation that loses a placeholder or formatting is discarded
  and that text stays English. Messages the bot sends on its own (feedback questions, download notifications)
  use the language last used in the group. A suggestion card keeps its language while browsing. The web
  interface is available in English and German.

## When the AI provider is unreachable

When the spend limit is reached or Claude (or the other provider) has an outage, the bot says so once in the
group, stops taking requests and checks every 5 minutes. As soon as the provider answers again: "✅ Corsarr is
back." Sonarr/Radarr download notifications keep arriving in the meantime (they don't need the AI provider).
