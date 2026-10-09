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
| `@bot no more pirate` · `@bot Gen Z back on` | characters off/on |

The same settings are in the web interface under *Configuration → Bot behaviour*.

## Characters and language

- 🏴‍☠️ **Pirate** and 📱 **Gen Z** take turns at random or have a short exchange. If only one is active, only
  that one speaks; with both off the bot writes plainly.
- The characters avoid repeating themselves: the bot remembers its latest phrasings.
- **Language:** the bot speaks English and German and answers in the language you write in – text, suggestion
  card, buttons, reasons and TMDB plot summaries. Messages in any other language get an English answer. Messages it sends on its own (feedback questions, download notifications) use the language last used in the group. A suggestion card keeps its language while browsing.

## When the AI provider is unreachable

When the spend limit is reached or Claude (or the other provider) has an outage, the bot says so once in the
group, stops taking requests and checks every 5 minutes. As soon as the provider answers again: "✅ Corsarr is
back." Sonarr/Radarr download notifications keep arriving in the meantime (they don't need the AI provider).
