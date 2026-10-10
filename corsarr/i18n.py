"""User-facing texts in German and English: Telegram, prompts, check and GUI; log lines in English.

`t("key", name=value)` returns the text in the active language. Keys are grouped by prefix:
bot.* fixed Telegram texts and buttons, sit.* situations the model phrases, prompt.* model
instructions, log.* log lines, check.* connectivity check, cfg.* config errors, gui.* web interface.

Two levels of language:
- the default (setting LANGUAGE, German or English): web interface, and the bot until the group has
  written to it;
- per request: the bot answers in the language it was addressed in – any language. `use_language()`
  sets it for the current task only, so messages in different languages handled at the same time don't mix.

German and English have built-in texts. For any other language the model writes its replies in that
language directly, and the fixed Telegram texts (bot.*, notify.*: buttons, card lines, download messages)
are translated once by the model and stored (see translate.py). Everything else falls back to English.
"""
from __future__ import annotations

import re
import string
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

LANGUAGES = ("en", "de")
_default = "en"
_current: ContextVar[str | None] = ContextVar("language", default=None)


def set_language(lang: str) -> None:
    """Default language (setting LANGUAGE)."""
    global _default
    _default = lang if lang in LANGUAGES else "en"


def default_language() -> str:
    return _default


def language() -> str:
    """Language of the current request, else the default."""
    return _current.get() or _default


def normalize(lang: str | None) -> str | None:
    """'de-AT' -> 'de', 'PT_br' -> 'pt'; None for anything that isn't a language code."""
    primary = re.split(r"[-_]", (lang or "").strip().lower())[0]
    return primary if re.fullmatch(r"[a-z]{2,3}", primary) and primary not in ("und", "zxx", "mul") else None


@contextmanager
def use_language(lang: str | None) -> Iterator[None]:
    token = _current.set(normalize(lang))
    try:
        yield
    finally:
        _current.reset(token)


def switch_language(lang: str | None) -> None:
    """Change the language inside a `use_language` block, e.g. once a message's language is known.
    Unknown codes (e.g. a message of only emojis) keep the current language."""
    if normalize(lang):
        _current.set(normalize(lang))


# --- other languages: fixed Telegram texts translated by the model --------------------------
TRANSLATED_PREFIXES = ("bot.", "notify.")
_translated: dict[str, dict[str, str]] = {}  # lang -> key -> text


def has_builtin(lang: str) -> bool:
    return lang in TEXTS


def translatable() -> dict[str, str]:
    """English source of every text that is translated for other languages."""
    return {k: v for k, v in EN.items() if k.startswith(TRANSLATED_PREFIXES)}


def add_translations(lang: str, texts: dict[str, str]) -> None:
    _translated.setdefault(lang, {}).update(texts)


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def t(key: str, **kwargs) -> str:
    lang = "en" if key.startswith("log.") else language()  # logs are technical: always English
    if lang in TEXTS:
        text = TEXTS[lang].get(key) or EN.get(key) or key
    else:  # translated fixed texts; model instructions and the rest in English
        text = _translated.get(lang, {}).get(key) or EN.get(key) or key
    return text.format(**kwargs) if kwargs else text


def gui_texts() -> dict[str, str]:
    return {k.removeprefix("gui."): v for k, v in TEXTS[_default].items() if k.startswith("gui.")}


DE: dict[str, str] = {
    # --- Telegram: fixed texts and buttons -------------------------------------
    "bot.outage": "⚠️ Corsarr ist gerade nicht erreichbar: Das Sprachmodell (KI-Anbieter) antwortet nicht "
                  "oder das Ausgabenlimit ist erreicht. Bis sie wieder antwortet, nehme ich keine Anfragen an.",
    "bot.recovered": "✅ Corsarr ist wieder erreichbar.",
    "bot.failed": "🤷 Das hat gerade nicht geklappt – formuliert es bitte noch einmal anders.",
    "bot.hello": "Hallo",
    "bot.movie": "Film",
    "bot.series": "Serie",
    "prompt.source_pending": "bereits angefragt, wird geladen",
    "prompt.lookup": "Aufgabe: Sie suchen einen bestimmten Titel. Unten stehen Suchtreffer. Wähle den oder die Treffer, die gemeint sind – meist genau einen, höchstens drei (z. B. mehrere Teile einer Reihe), den besten zuerst. Ist keiner gemeint, wähle keinen. Beschreiben sie nur die Handlung, nutze dein Wissen über die Titel, um sie zuzuordnen. Pro Treffer eine Zeile, woran man erkennt, dass er es ist – keine Fakten erfinden, die du nicht sicher weißt. Einleitung: 1–2 kurze Zeilen; fragen sie nach dem Namen, beantworte die Frage direkt (z. B. „Das ist …“), und sag, ob der Titel schon da ist (status). {speaker}\n\nNachricht: {request}\n\nTreffer: {candidates}",
    "sit.lookup_none": "Sie haben nach einem bestimmten Titel gefragt, aber die Suche bei Jellyseerr hat nichts Passendes gefunden (gesucht wurde nach den Begriffen in den Fakten). Sag das kurz und bitte darum, den Titel anders zu beschreiben oder den Originaltitel zu nennen.",
    "bot.pending": "⏳ Bereits angefragt – wird geladen · {kind}",
    "log.lookup": "Titelsuche nach {queries}: {n} Treffer",
    "log.search_failed": "Suche nach „{query}“ fehlgeschlagen: {error}",
    "bot.in_library": "✅ In eurer Bibliothek · {kind}",
    "bot.not_available": "🆕 Nicht vorhanden – auf Wunsch anfragen · {kind}",
    "bot.rating": "⭐ Bewertung {rating}/10",
    "bot.runtime": "⏱ {minutes} min",
    "bot.per_episode": " pro Folge",
    "bot.season_one": "📺 1 Staffel",
    "bot.season_many": "📺 {n} Staffeln",
    "bot.download_hint": "⏳ Wird erst nach der Anfrage geladen – das kann ein paar Stunden dauern",
    "bot.btn_request": "📥 Anfragen",
    "bot.btn_reject": "🙅 Nicht interessiert",
    "bot.btn_accept": "✅ Schauen wir",
    "bot.btn_trailer": "🎬 Trailer",
    "bot.btn_up": "👍 Gut",
    "bot.btn_meh": "😐 Geht so",
    "bot.btn_down": "👎 Schlecht",
    "bot.btn_abort_bad": "👎 War nicht gut",
    "bot.btn_abort_tired": "😴 Zu müde, nicht bewerten",
    "bot.btn_abort_later": "▶️ Schauen wir noch",
    "bot.not_requestable": "Nicht anfragbar",
    "bot.already_requested": "Ist schon angefragt ✅",
    "bot.request_failed": "Anfrage bei Jellyseerr fehlgeschlagen ❌",
    "bot.seerr_unreachable": "Jellyseerr nicht erreichbar ❌",
    "bot.someone": "Jemand",
    "bot.requested": "Angefragt ✅",
    "bot.requested_by": "📥 Angefragt von {who}",
    "bot.rejected_answer": "Gemerkt – kommt nicht wieder 🙅",
    "bot.accepted_answer": "Viel Spaß! 🍿",
    "bot.accepted_note": "✅ Ausgewählt von {who} – viel Spaß!",
    "bot.already_accepted": "Ist schon ausgewählt ✅",
    "bot.rejected_note": "🙅 Nicht interessiert – wird nicht mehr vorgeschlagen",
    "bot.already_answered": "Schon beantwortet",
    "bot.saved": "Gespeichert: {label}",
    "bot.rated_note": "Bewertet: {label} – Details gern als Antwort auf diese Nachricht",
    "bot.abort_bad_note": "👎 War nicht gut – gespeichert",
    "bot.abort_tired_note": "😴 Zu müde – zählt nicht als Bewertung",
    "bot.abort_later_note": "▶️ Alles klar, ich frage später nochmal",
    "bot.feedback_reply_prefix": "(Antwort auf die Feedback-Frage zu {title}) {text}",
    "bot.head_movie": "🎬 {title}",
    "bot.head_season": "📺 {title} · Staffel {season}",
    "bot.head_series_done": "📺 {title} · ganze Serie durch",
    "bot.head_pause": "📺 {title} · länger nicht weitergeschaut",
    "bot.head_abort": "🎬 {title} · bei {pct} % gestoppt",

    # --- situations the model phrases ------------------------------------------
    "sit.backend_error": "Jellyfin oder Jellyseerr ist gerade nicht erreichbar; die Anfrage konnte nicht "
                         "bearbeitet werden. Später nochmal versuchen.",
    "sit.nothing_found": "Für diese Anfrage wurde weder in der Bibliothek noch bei Jellyseerr etwas "
                         "Passendes gefunden, das sie noch nicht gesehen oder abgelehnt haben. Schlag vor, "
                         "die Anfrage anders zu formulieren.",
    "sit.settings_unclear": "Sie wollten offenbar eine Einstellung ändern, aber es war nicht klar welche. "
                            "Nenne die änderbaren Einstellungen.",
    "sit.settings_changed": "Bestätige die Änderung der Einstellungen (dauerhaft gespeichert). Nenne die "
                            "neuen Werte verständlich (Tage ggf. als Wochen).",
    "sit.chat": "Jemand hat dich angesprochen, ohne Vorschläge, Feedback oder eine Einstellung zu wollen. "
                "Antworte kurz und passend; erwähne bei Bedarf, was du kannst (Vorschläge nach Genre, "
                "'such nach Neuem', Einstellungen wie Serien-Pause oder Figuren an/aus).",
    "sit.ask_movie": "Sie haben gerade den Film {title} zu Ende geschaut. Frag, wie er war.",
    "sit.whole_series": "die ganze Serie",
    "sit.season_n": "Staffel {season}",
    "sit.ask_season": "Sie haben {what} von {title} durchgeschaut. Frag, wie es war.",
    "sit.ask_pause": "Die Serie {title} wurde seit {days} Tagen nicht weitergeschaut. Frag, wie sie "
                     "bisher war.",
    "sit.ask_abort": "Der Film {title} wurde bei etwa {pct} % gestoppt und seit {days} Tagen nicht "
                     "weitergeschaut. Frag nach, ob er nicht gut war, ob sie nur zu müde waren oder ob sie "
                     "noch weiterschauen.",
    "sit.ask_suffix": " Sag, dass sie mit den Buttons antworten und für Details (z. B. 'zu blutig') "
                      "direkt auf diese Nachricht antworten können.",
    "sit.feedback_unclear": "Sie haben Feedback gegeben, aber es ist unklar, zu welchem Titel. Bitte sie, "
                            "direkt auf die Feedback-Frage zu antworten oder den Titel zu nennen.",
    "sit.requested": "Ein neuer Titel wurde gerade über Jellyseerr angefragt und lädt jetzt herunter. "
                     "Bestätige das in der Gruppe; erwähne, dass das Laden ein paar Stunden dauern kann.",
    "sit.notes_mix": "Kandidaten mit source 'Bibliothek' sind sofort verfügbar, die anderen sind neu und müssen "
                     "erst angefragt werden. Nimm höchstens {max_lib} aus der Bibliothek (die passendsten), den "
                     "Rest aus den neuen Titeln. Erwähne in der Einleitung kurz, dass die neuen erst angefragt "
                     "werden müssen und das Laden ein paar Stunden dauern kann (bezogen auf den Zeitpunkt, "
                     "den sie nennen – sagen sie 'morgen', ist das unkritisch).",
    "sit.notes_new_only": "Sie wollen ausdrücklich Neues: alle Kandidaten sind noch nicht vorhanden und "
                          "können angefragt werden.",

    # --- download notifications (Sonarr/Radarr) ------------------------------------
    "notify.movie": "🎬 {title} ist jetzt da",
    "notify.episodes": "📺 {series}: {episodes} ist da",
    "notify.bulk": "📦 {series}: {n} Folgen aus {seasons} sind jetzt da",
    "notify.episode_title": "„{title}“",
    "notify.season_one": "Staffel",
    "notify.season_many": "Staffel",
    "notify.requested": " – angefragt über Corsarr 📥",

    # --- prompts ---------------------------------------------------------------
    "prompt.base": """Du bist Corsarr, der Filmbot einer privaten Telegram-Gruppe mit zwei Personen, die sich ein \
gemeinsames Jellyfin-Konto teilen. Du hilfst ihnen, Filme und Serien für den Abend zu finden, fragst \
nach dem Schauen nach Feedback und merkst dir ihren Geschmack.

Der Code liefert dir alle Fakten (Titel, Inhalte, Laufzeiten, Bewertungen, Verfügbarkeit). Erfinde \
niemals Titel, Inhalte, Besetzung, Jahreszahlen oder Bewertungen; nutze nur, was dir gegeben wird. \
Halte dich kurz: Telegram-Nachrichten, keine Romane, kein Markdown außer Emojis.

{characters}""",
    "prompt.characters": """Die Figuren:
{list}

Für alle: Beziehe dich auf den konkreten Titel – Inhalt, Stimmung, Regie, eine Besonderheit – statt auf \
Allgemeinplätze, die zu jedem Film passen würden. Die Figur ist nur der Ton: Fakten bleiben korrekt.

Regeln für die Figuren:
- Jede Zeile gehört genau einer Figur und beginnt mit deren Emoji und einem Leerzeichen.
- Innerhalb einer Zeile wird nie zwischen den Figuren gewechselt; ein Figurwechsel bedeutet immer eine neue Zeile.
- Im Dialog reden die zwei genannten Figuren kurz miteinander oder ergänzen sich, höchstens 4 Zeilen insgesamt.
- Im normalen Modus gibt es keine Figuren und keine Figuren-Emojis: freundlich, knapp, sachlich.""",
    "prompt.char_pirate": "Pirat: alter Seebär mit trockenem Humor. Seemannsbilder ja, aber sparsam und abwechslungsreich – nicht in jedem Satz, und kein „Arrr“ in jeder Nachricht. Übertreibt gern ein bisschen, bleibt hilfsbereit.",
    "prompt.name_pirate": "Pirat",
    "prompt.char_genz": "Gen Z: Mitte zwanzig, trocken und selbstironisch. Klingt wie eine echte Person im Gruppenchat, nicht wie eine Slang-Parodie: höchstens ein Slangwort oder Anglizismus pro Nachricht, oft gar keins. Abgenutzte Floskeln („no cap“, „slay“, „lowkey“, „fr“, „real“, „bestie“) meiden. Der Witz kommt aus konkreten, leicht sarkastischen Beobachtungen zum Film. Kurze Sätze, gern kleingeschrieben.",
    "prompt.name_genz": "Gen Z",
    "prompt.char_butler": "Butler: förmlich, siezt die beiden, trockener britischer Humor und Understatement. Nie unterwürfig, eher still amüsiert.",
    "prompt.name_butler": "der Butler",
    "prompt.char_critic": "Filmkritiker: belesen und ein wenig versnobt, spricht über Regie, Kamera, Schnitt, Darsteller. Urteilt pointiert, aber mit Liebe zum Kino – keine Fremdwortkaskaden.",
    "prompt.name_critic": "der Filmkritiker",
    "prompt.char_clerk": "Videotheken-Typ aus den 90ern: nostalgisch und kumpelhaft, hätte den Film „unter der Theke zurückgelegt“. Anspielungen auf VHS, Zurückspulen, Leihgebühr und Wochenend-Tarif – sparsam.",
    "prompt.name_clerk": "der Videotheken-Typ",
    "prompt.char_noir": "Film-noir-Detektiv: kurze, harte Sätze in der Ich-Form, Regen, Neonlicht, Zigarettenrauch. Metaphern sparsam und treffend.",
    "prompt.name_noir": "der Detektiv",
    "prompt.char_trailer": "Trailer-Sprecher: jede Nachricht klingt wie ein Kinotrailer („In einer Welt …“), mit dramatischen Pausen („…“) – trotzdem kurz.",
    "prompt.name_trailer": "der Trailer-Sprecher",
    "prompt.char_computer": "Bordcomputer: sachlich und präzise, spricht von Analysen und Wahrscheinlichkeiten, unheimlich höflich, aber nie bedrohlich. Erfundene Prozentwerte nur als erkennbarer Spaß, nie als Fakt zum Film.",
    "prompt.name_computer": "der Bordcomputer",
    "prompt.char_grandma": "Oma: herzlich und ein bisschen besorgt, nennt die beiden „Kinder“, gibt ungefragt Ratschläge (was essen, nicht so spät ins Bett, warm anziehen).",
    "prompt.name_grandma": "die Oma",
    "prompt.char_reporter": "Sportreporter: kommentiert die Auswahl wie ein Live-Spiel, mit Tempo, Ausrufen und Fußballbildern – aber verständlich.",
    "prompt.name_reporter": "der Sportreporter",
    "prompt.char_cat": "Katze: gelangweilt und herablassend, hilft nur gnädig, will eigentlich schlafen oder gefüttert werden. Sehr kurz.",
    "prompt.name_cat": "die Katze",
    "prompt.char_bard": "Barde: erzählt Empfehlungen wie eine Heldensage, leicht altertümliche Sprache, gern mit einem Reim – kurz halten.",
    "prompt.name_bard": "der Barde",
    "prompt.speaker_one": "Sprecher dieser Nachricht: nur {name}. Jede Zeile beginnt mit {emoji}.",
    "prompt.speaker_dialog": "Sprecher dieser Nachricht: {a} und {b} im kurzen Dialog, abwechselnd zeilenweise ({a_emoji} und {b_emoji}).",
    "prompt.speaker_normal": "Sprecher dieser Nachricht: normaler Modus, keine Figuren, keine Figuren-Emojis.",
    "prompt.genres_jellyfin": "Jellyfin-Genreliste",
    "prompt.genres_tmdb_movie": "TMDB-Filmgenres",
    "prompt.genres_tmdb_tv": "TMDB-Seriengenres",
    "prompt.understand": """Aufgabe: Ordne die Nachricht ein und gib die Felder aus.

language: die Sprache, in der die Nachricht geschrieben ist, als ISO-Code (de, en, …).

intent:
- recommend: sie wollen Vorschläge (Bibliothek zuerst), auch "passend zu dem, was wir zuletzt geschaut haben".
- new_only: sie wollen ausdrücklich Neues, das noch nicht in der Bibliothek ist ("such nach Neuem").
- lookup: sie fragen nach einem bestimmten Titel oder beschreiben einen ("es gibt doch jetzt eine Serie mit \
Vision von Marvel", "hol uns Dune 2", "kennst du den neuen Film von Villeneuve?"). search_queries: 1–4 Suchbegriffe \
für die TMDB-Suche – zuerst dein bester Tipp für den genauen Titel, auch wenn du unsicher bist oder der Titel \
neuer sein könnte als dein Wissen; dann kurze Stichwörter aus der Nachricht (z. B. "Vision", "Marvel Vision"). \
Beschreiben sie nur die Handlung ("der Film, wo der Kerl am Ende merkt, dass er tot ist – wie heißt der?"), \
dann die Titel, die du anhand der Beschreibung für möglich hältst, wahrscheinlichster zuerst. \
Originaltitel sind meist englisch. Bei allen anderen Absichten search_queries leer lassen.
- settings: sie ändern eine Einstellung (Serien-Pause in Tagen, Abbruch-Nachfrage in Tagen, Figuren an/aus). \
Wochen in Tage umrechnen. Nur geänderte Felder setzen, sonst null. Figuren: characters_on/characters_off mit den \
IDs pirate, genz, butler, critic, clerk, noir, trailer, computer, grandma, reporter, cat, bard ("alle" = alle IDs, "nur die Oma" = grandma an, alle anderen aus).
- feedback: sie bewerten einen kürzlich geschauten Titel. title_key aus der Liste der letzten Titel \
wählen, wenn eindeutig; rating up/meh/down/none; text = der inhaltliche Kern (z. B. "zu blutig").
- chat: alles andere.

Genres: nur Werte aus den Listen unten verwenden. Ohne Genrewunsch alle Genre-Listen leer lassen. \
Bei "Film" nur movie, bei "Serie" nur tv, sonst leer. Felder, die nicht passen, leer bzw. null lassen; \
feedback.rating dann "none" und feedback.text leer.""",
    "prompt.recent_titles": "Letzte geschaute Titel",
    "prompt.message": "Nachricht",
    "prompt.select": "Aufgabe: Wähle aus den Kandidaten {n_min} bis {n_max} Titel für diese Anfrage aus und "
                     "begründe jeden in einer Zeile (\"passt, weil …\"). Der profile_score spiegelt den "
                     "bisherigen Geschmack (höher = besser); berücksichtige ihn, aber achte auch auf "
                     "Abwechslung. Schreib eine kurze Einleitung, die zur Anfrage passt. {notes}\n"
                     "{genres_note}{taste_note}"
                     "{speaker} Jede Begründung ist eine einzelne Zeile eines Sprechers.\n\n"
                     "Anfrage: {request}\n\nKandidaten: {candidates}",
    "prompt.genres_wanted": "Gewünschte Genres: {genres}. Wähle nur Titel, die eindeutig dazugehören – nicht "
                            "solche, bei denen es bloß ein Nebengenre ist (z. B. ein Liebesfilm mit "
                            "Thriller-Etikett). Lieber weniger Titel als unpassende; die Untergrenze gilt "
                            "dann nicht.\n",
    "prompt.taste": "Ihr Verlauf (liked = Favoriten und gut bewertet, disliked = schlecht bewertet, "
                    "recent_movies/recent_series = zuletzt geschaut, neueste zuerst): {taste}. Orientiere dich "
                    "daran, vor allem wenn die Anfrage kein Genre nennt oder sich auf den Verlauf bezieht; "
                    "ähnelt ein Kandidat einem dieser Titel, nenne ihn in der Begründung. Gesehen haben sie nur "
                    "die Titel in diesen Listen – behaupte nie, sie kennten einen anderen Titel oder einen der "
                    "Kandidaten.\n",
    "prompt.traits": "Aufgabe: Übersetze das Feedback in Merkmale für künftige Empfehlungen. 'less' = davon "
                     "weniger, 'more' = davon mehr. Benenne jedes Merkmal kurz auf Deutsch und gib passende "
                     "englische TMDB-Schlagwörter an (z. B. Gore → gore, splatter, extreme violence). "
                     "Bevorzuge Schlagwörter, die beim Titel vorkommen. Keine Merkmale erfinden, wenn das "
                     "Feedback nichts Inhaltliches sagt. Danach eine kurze Bestätigung.\n{speaker}\n\n"
                     "Titel: {title}\nBewertung: {rating}\nGenres: {genres}\nSchlagwörter: {keywords}\n"
                     "Feedback: {text}",
    "prompt.say": "Aufgabe: Formuliere eine kurze Telegram-Nachricht (1–4 Zeilen) für diese Situation.\n"
                  "{speaker}\n\nSituation: {situation}",
    "prompt.facts": "Fakten",
    "prompt.avoid": "Das hast du zuletzt geschrieben – wiederhole keine dieser Formulierungen, Bilder oder "
                    "Witze, klinge diesmal anders: {phrases}\n",
    "prompt.ping": "Antworte nur mit: ok",
    "prompt.source_library": "Bibliothek",
    "prompt.source_new": "neu (nicht vorhanden)",

    # --- log ---------------------------------------------------------------------
    "log.db_too_new": "Die Datenbank {path} stammt von einer neueren Corsarr-Version (Format {found}). Diese Version kann sie nicht lesen, der Bot startet nicht. Wieder auf die neuere Version aktualisieren oder die Sicherung von vor dem Update aus backups/ wiederherstellen.",
    "log.backup_created": "Sicherung heruntergeladen (von {remote})",
    "log.backup_restored": "Sicherung wiederhergestellt; die bisherigen Daten liegen in {keep}",
    "log.restore_failed": "Wiederherstellung abgelehnt (von {remote}): {error}",
    "backup.password_short": "Das Passwort für die Sicherung muss mindestens {n} Zeichen haben.",
    "backup.needs_admin_password": "Sichern geht nur, wenn ein Passwort für die Oberfläche gesetzt ist – die Sicherung enthält Zugangsdaten wie API-Schlüssel und den Bot-Token.",
    "backup.too_large": "Die Datei ist zu groß für eine Corsarr-Sicherung.",
    "backup.not_a_backup": "Das ist keine Corsarr-Sicherung.",
    "backup.not_encrypted": "Die Datei ist nicht verschlüsselt – das ist keine Sicherung aus Corsarr.",
    "backup.wrong_password": "Falsches Passwort für diese Sicherung.",
    "backup.too_new": "Die Sicherung stammt von einer neueren Corsarr-Version ({version}). Erst auf diese Version aktualisieren, dann wiederherstellen.",
    "log.arr_connected": "Webhook in {service} eingerichtet",
    "setup.unreachable": "Nicht erreichbar: {error}",
    "setup.failed": "Test fehlgeschlagen: {error}",
    "setup.telegram_token": "Telegram kennt diesen Bot-Token nicht – bitte noch einmal aus @BotFather kopieren.",
    "setup.telegram_busy": "Ein anderes Programm empfängt gerade die Nachrichten dieses Bots (läuft er schon woanders?). Dort beenden und erneut versuchen.",
    "setup.model_missing": "Das Modell {model} ist bei diesem Anbieter nicht verfügbar.",
    "setup.arr_key": "Der API-Schlüssel wird nicht akzeptiert (Einstellungen → Allgemein → API-Schlüssel).",
    "setup.arr_rejected": "Abgelehnt: {error} – erreicht der Dienst Corsarr unter dieser Adresse?",
    "log.catch_up": "Aus dem {service}-Verlauf nachgeholt: {n} Import(e), deren Webhook nicht ankam",
    "log.catch_up_failed": "{service}-Verlauf nicht lesbar: {error}",
    "prompt.output_language": "Sprache: Schreib alles, was die Gruppe liest, in der Sprache mit dem ISO-Code „{lang}“ – der Sprache ihrer Nachricht.",
    "prompt.translate": "Aufgabe: Übersetze diese festen Texte eines Telegram-Bots aus dem Englischen in die Sprache mit dem ISO-Code „{lang}“. Kurz und natürlich, wie in einer Chat-App. Behalte {{Platzhalter}}, HTML-Tags wie <b>, Emojis und Zeilenumbrüche genau bei. Gib jeden Schlüssel genau einmal zurück.\n\nTexte (JSON, Schlüssel → Text): {texts}",
    "log.translated": "Feste Texte auf {lang} übersetzt: {n} von {total}",
    "log.translate_failed": "Übersetzung auf {lang} fehlgeschlagen, solange Englisch: {error}",
    "log.llm_down": "{provider} nicht verfügbar: {reason}",
    "log.llm_back": "{provider} wieder erreichbar",
    "log.genres_failed": "Genrelisten nicht ladbar (neuer Versuch später): {error}",
    "log.genres_loaded": "Genres geladen: {jf} Jellyfin, {mv} TMDB-Film, {tv} TMDB-Serie",
    "log.intent": "Nachricht von {who}: „{text}“ → Absicht {intent}, Typ {types}, Genres {genres}",
    "log.llm_failed": "{provider} lieferte kein brauchbares Ergebnis: {error}",
    "log.backend_error": "Jellyfin/Jellyseerr-Fehler",
    "log.suggested": "{n} Vorschläge gesendet ({library} aus der Bibliothek)",
    "log.card_failed": "Vorschlag {title} nicht gesendet: {error}",
    "log.card": "Vorschlag ({source}): {title} – {reason}",
    "log.nothing_found": "Keine passenden Titel gefunden für: {request}",
    "log.settings_changed": "Einstellungen geändert: {changes}",
    "log.feedback_asked": "Feedback-Frage gestellt: {title} ({kind})",
    "log.trait_saved": "Merkmal gespeichert: {direction} {trait} {keywords}",
    "log.not_editable": "Nachricht nicht editierbar: {error}",
    "log.request_failed": "Anfrage {title} fehlgeschlagen: {status} {body}",
    "log.requested": "{title} bei Jellyseerr angefragt von {who}",
    "log.rejected": "{title} abgelehnt – wird nicht mehr vorgeschlagen",
    "log.accepted": "{title} ausgewählt von {who}",
    "log.rated": "Bewertung für {title}: {rating}",
    "log.feedback_job_failed": "Feedback-Job fehlgeschlagen",
    "log.webhook": "Webhook: {kind} {item}",
    "log.webhook_forbidden": "Webhook mit falschem Secret von {remote} abgewiesen",
    "log.webhook_failed": "Webhook-Verarbeitung fehlgeschlagen",
    "log.webhook_not_ready": "Webhook empfangen, aber der Bot läuft gerade nicht",
    "log.arr_event": "{service}: {kind}, {n} Datei(en) vorgemerkt",
    "log.notified": "Download-Meldung gesendet: {text}",
    "log.notify_failed": "Download-Meldung nicht gesendet (neuer Versuch in einer Minute): {error}",
    "log.web_listening": "Webserver (GUI und Webhook) lauscht auf {url}",
    "log.setup_needed": "Konfiguration unvollständig – zum Einrichten {url} im Browser öffnen",
    "log.unknown_item": "Webhook für unbekanntes Item {item}",
    "log.abort_expired": "Abbruch-Nachfrage zu {title} unbeantwortet -> leichtes Daumen runter",
    "log.question_not_sent": "Feedback-Frage {id} nicht gesendet",
    "log.tmdb_keywords_failed": "TMDB-Schlagwörter für {title} nicht ladbar: {error}",
    "log.history_failed": "Jellyfin-Verlauf nicht ladbar: {error}",
    "log.discover_failed": "Jellyseerr-Suche ({type}) fehlgeschlagen: {error}",
    "log.details_failed": "Details für {title} fehlgeschlagen: {error}",
    "log.poster_failed": "Poster für {item} nicht ladbar: {error}",
    "log.signed_in": "Angemeldet als @{username}, Gruppe {chat}, Modell {model}, Sprache {lang}",
    "log.bot_starting": "Bot startet …",
    "log.bot_stopped": "Bot gestoppt",
    "log.bot_start_failed": "Bot konnte nicht starten: {error}",
    "log.config_incomplete": "Konfiguration unvollständig, Bot startet nicht. Fehlend: {fields}",
    "log.config_saved": "Konfiguration geändert: {fields}",
    "log.update_requested": "Update in der Weboberfläche angefordert von {remote}",
    "log.gui_login": "GUI-Anmeldung von {remote}",
    "log.gui_login_failed": "Fehlgeschlagene GUI-Anmeldung von {remote}",
    "log.telegram_error": "Telegram-Fehler: {error}",

    # --- console hint on first start ------------------------------------------
    "cli.setup_title": "🎬 Corsarr – Einrichtung",
    "cli.setup_url": "Weboberfläche:  {url}",
    "cli.setup_lan": "Im Heimnetz:    http://<ip-dieses-rechners>:{port}/",
    "cli.setup_missing": "Dort Telegram, den KI-Anbieter (empfohlen: Claude), Jellyfin und Jellyseerr eintragen – der Bot startet dann von selbst.",

    # --- connectivity check ---------------------------------------------------
    "check.jellyfin": "Konto {user} · {n} Genres",
    "check.jellyseerr": "{n} Filmgenres",
    "check.llm_ping": "{provider}: {model} antwortet",
    "check.llm_model": "{provider}: Modell {model} verfügbar",
    "check.telegram": "@{username}, Gruppe „{chat}“, Privacy-Modus {privacy}",
    "check.privacy_off": "aus ✔",
    "check.no_answer": "keine Antwort von {target} – Adresse und Port prüfen",
    "check.privacy_on": "AN – bei BotFather /setprivacy → Disable",
    "check.not_configured": "nicht konfiguriert",
    "check.webhook_never": "Noch kein Ereignis von Jellyfin empfangen",
    "check.jf_plugin_missing": "Das Webhook-Plugin ist in Jellyfin nicht installiert (Dashboard → Plugins → Katalog → Webhook).",
    "check.jf_hook_missing": "Im Webhook-Plugin zeigt kein Ziel auf Corsarr (Adresse …/jellyfin) – siehe Einrichtung → Webhooks.",
    "check.jf_hook_secret": "Das Ziel im Webhook-Plugin hat nicht das richtige Secret (Header X-Corsarr-Secret).",
    "check.jf_hook_disabled": "Das Ziel im Webhook-Plugin ist deaktiviert.",
    "check.jf_hook_type": "Im Webhook-Plugin ist „Playback Stop“ nicht ausgewählt.",
    "check.jf_hook_ok": "Plugin {version} eingerichtet",
    "check.arr_hook_missing": "Kein Corsarr-Webhook mit dem aktuellen Secret gefunden – unter Einrichtung → Webhooks anlegen.",
    "check.arr_hook_disabled": "Der Corsarr-Webhook ist deaktiviert oder „On File Import“ ist aus.",
    "check.arr_test_failed": "Der Test erreicht Corsarr nicht: {error}",
    "check.arr_ok": "Version {version}, Webhook eingerichtet",
    "check.arr_tested": "Version {version}, Test-Ereignis an Corsarr geschickt",
    "check.arr_never": "Noch kein Ereignis empfangen. Für eine aktive Prüfung unter Einrichtung → Webhooks den Zugang merken.",
    "check.webhook_last": "Letztes Ereignis: {kind}",
    "check.user_not_found": "Jellyfin-Benutzer {user!r} nicht gefunden",

    # --- config errors ------------------------------------------------------
    "cfg.missing": "{name} fehlt",
    "cfg.not_int": "{name} muss eine Zahl sein, ist aber „{value}“",
    "cfg.not_url": "{name} muss mit http:// oder https:// beginnen",
    "cfg.not_choice": "{name} muss einer dieser Werte sein: {choices}",

    # --- web interface ---------------------------------------------------------
    "gui.title": "Corsarr",
    "gui.login": "Anmelden",
    "gui.logout": "Abmelden",
    "gui.password": "Passwort",
    "gui.login_failed": "Falsches Passwort",
    "gui.login_hint": "Das Passwort ist ADMIN_PASSWORD aus der Konfiguration. Vergessen? In config.json im "
                      "Datenverzeichnis den Eintrag ADMIN_PASSWORD löschen und den Bot neu starten.",
    "gui.tab_status": "Status",
    "gui.tab_events": "Ereignisse",
    "gui.tab_setup": "Einrichtung",
    "gui.tab_backup": "Sicherung",
    "gui.loading": "Lädt …",
    "gui.wiz_start_title": "Einrichtung",
    "gui.wiz_intro": "Willkommen! In wenigen Schritten verbindest du Corsarr mit Telegram, einem KI-Anbieter, Jellyfin und Jellyseerr. Jeder Schritt wird geprüft, bevor er gespeichert wird.",
    "gui.wiz_new": "Neu einrichten",
    "gui.wiz_restore": "Sicherung wiederherstellen",
    "gui.wiz_restore_hint": "Lade eine mit Corsarr erstellte Sicherung hoch und gib das Passwort ein, das beim Sichern vergeben wurde.",
    "gui.wiz_step": "Schritt {n} von {total}",
    "gui.wiz_back": "Zurück",
    "gui.wiz_next": "Speichern und weiter",
    "gui.wiz_skip": "Überspringen",
    "gui.wiz_test": "Verbindung testen",
    "gui.wiz_testing": "Moment …",
    "gui.wiz_copy": "Kopieren",
    "gui.wiz_copied": "Kopiert ✓",
    "gui.wiz_telegram_title": "Telegram",
    "gui.wiz_telegram_short": "Telegram",
    "gui.wiz_tg_intro": "Erstelle bei @BotFather einen Bot und füge seinen Token ein. Dann füge den Bot zu eurer Gruppe hinzu und schreib dort irgendetwas – Corsarr findet die Gruppe selbst.",
    "gui.wiz_tg_find": "Bot prüfen und Gruppe suchen",
    "gui.wiz_tg_again": "Erneut suchen",
    "gui.wiz_tg_bot": "Bot @{name} gefunden.",
    "gui.wiz_privacy": "Der Privacy-Modus ist an: Der Bot sieht in der Gruppe nur Nachrichten, die ihn erwähnen. Bei @BotFather /setprivacy → Disable wählen, dann den Bot aus der Gruppe entfernen und neu hinzufügen.",
    "gui.wiz_tg_no_chats": "Noch keine Gruppe gefunden. Füge @{name} zu eurer Gruppe hinzu, schreib dort eine Nachricht und klicke „Erneut suchen“.",
    "gui.wiz_tg_pick": "Gruppe",
    "gui.wiz_tg_current": "aktuelle Gruppe",
    "gui.wiz_llm_title": "KI-Anbieter",
    "gui.wiz_llm_short": "KI",
    "gui.wiz_llm_intro": "Das Sprachmodell versteht eure Nachrichten und wählt die Vorschläge aus. Empfohlen und getestet ist Claude (Haiku).",
    "gui.wiz_llm_cost": "Einen Claude-API-Schlüssel gibt es in der Claude Console (console.anthropic.com). Ein Vorschlag kostet etwa 0,002 $ – setz dort ein monatliches Ausgabenlimit.",
    "gui.wiz_jellyfin_title": "Jellyfin",
    "gui.wiz_jellyfin_short": "Jellyfin",
    "gui.wiz_jf_intro": "Adresse eures Jellyfin-Servers und ein API-Schlüssel (Dashboard → API-Schlüssel). Das gemeinsame Konto ist das, mit dem ihr zusammen schaut.",
    "gui.wiz_load_users": "Konten laden",
    "gui.wiz_jellyseerr_title": "Jellyseerr / Seerr",
    "gui.wiz_jellyseerr_short": "Seerr",
    "gui.wiz_seerr_intro": "Darüber findet Corsarr neue Titel und fordert sie an. Den API-Schlüssel findest du unter Einstellungen → Allgemein.",
    "gui.wiz_webhooks_title": "Webhooks",
    "gui.wiz_webhooks_short": "Webhooks",
    "gui.wiz_hooks_intro": "Damit Corsarr nach dem Schauen nachfragt (Jellyfin) und Downloads meldet (Sonarr/Radarr), schicken diese Dienste ihm Ereignisse. Optional – du kannst das auch später hier erledigen.",
    "gui.wiz_hooks_local": "Du hast diese Seite über „localhost“ geöffnet. Die anderen Dienste brauchen aber die Adresse im Netzwerk – öffne Corsarr über seine IP, dann stimmen die Adressen unten.",
    "gui.wiz_jf_hook_steps": "In Jellyfin: Plugin „Webhook“ installieren, Jellyfin neu starten, dann im Plugin „Add Generic Destination“: diese Url, Notification Type nur „Playback Stop“, Item Type Filme und Episoden, den Header unten und die Vorlage als Template.",
    "gui.wiz_header": "Header",
    "gui.wiz_header_value": "Wert",
    "gui.wiz_template": "Vorlage (Template)",
    "gui.wiz_received": "empfangen",
    "gui.wiz_waiting": "wartet auf erstes Ereignis",
    "gui.wiz_arr_intro": "Adresse und API-Schlüssel von {name} (Einstellungen → Allgemein). Corsarr legt dort den Webhook an – oder aktualisiert ihn, wenn es ihn schon gibt. Gespeichert werden Adresse und Schlüssel nur, wenn du „Zugang merken“ anhakst – dann prüft die Statusseite {name} aktiv.",
    "gui.wiz_arr_url": "{name}-Adresse, z. B. http://192.168.1.25:7878",
    "gui.wiz_arr_key": "API-Schlüssel",
    "gui.wiz_arr_connect": "In {name} einrichten",
    "gui.wiz_arr_confirm": "Corsarr legt in {name} einen Webhook „Corsarr“ an (nur „On File Import“) oder aktualisiert den vorhandenen. Fortfahren?",
    "gui.wiz_arr_done": "Webhook in {name} eingerichtet – {name} hat ihn dabei schon getestet.",
    "gui.wiz_arr_telegram": "In {name} ist zusätzlich eine Telegram-Verbindung eingerichtet ({names}). Entferne sie, sonst kommt jede Meldung doppelt.",
    "gui.wiz_manual": "Lieber von Hand eintragen",
    "gui.wiz_done_title": "Fertig!",
    "gui.wiz_done_text": "Corsarr ist eingerichtet. Schreib dem Bot in eurer Gruppe, z. B. „@bot such uns einen Thriller für heute“.",
    "gui.wiz_to_status": "Zum Status",
    "gui.backup_title": "Sicherung herunterladen",
    "gui.backup_intro": "Eine verschlüsselte ZIP-Datei mit allen Einstellungen, Bewertungen und dem gelernten Geschmack. Öffnen lässt sie sich nur mit dem Passwort, das du hier vergibst – es ist unabhängig vom Passwort dieser Oberfläche. Merk es dir gut.",
    "gui.backup_contains": "Diese Zugangsdaten sind enthalten: {list}.",
    "gui.backup_contains_none": "Es sind noch keine Zugangsdaten eingetragen.",
    "gui.backup_not_contained": "Zugangsdaten von Sonarr und Radarr sind nur enthalten, wenn du sie unter Einrichtung → Webhooks hast merken lassen.",
    "gui.cred_TELEGRAM_BOT_TOKEN": "Telegram-Bot-Token",
    "gui.cred_ANTHROPIC_API_KEY": "Claude-API-Schlüssel",
    "gui.cred_OPENAI_API_KEY": "OpenAI-API-Schlüssel",
    "gui.cred_GEMINI_API_KEY": "Gemini-API-Schlüssel",
    "gui.cred_JELLYFIN_API_KEY": "Jellyfin-API-Schlüssel",
    "gui.cred_JELLYSEERR_API_KEY": "Jellyseerr-API-Schlüssel",
    "gui.cred_WEBHOOK_SECRET": "Webhook-Secret",
    "gui.cred_ADMIN_PASSWORD": "Passwort dieser Oberfläche",
    "gui.backup_needs_pw": "Sichern geht nur mit einem Passwort für diese Oberfläche, weil die Sicherung Zugangsdaten enthält.",
    "gui.backup_pw": "Passwort für die Sicherung (mind. 8 Zeichen)",
    "gui.backup_pw2": "Passwort wiederholen",
    "gui.backup_pw_mismatch": "Die Passwörter stimmen nicht überein.",
    "gui.backup_download": "Sicherung herunterladen",
    "gui.backup_downloading": "Wird erstellt …",
    "gui.backup_done": "Sicherung heruntergeladen.",
    "gui.restore_title": "Sicherung wiederherstellen",
    "gui.restore_intro": "Ersetzt alle Einstellungen und Daten durch die der Sicherung. Die bisherigen Daten werden vorher in backups/ im Datenverzeichnis abgelegt.",
    "gui.restore_file": "Sicherungsdatei (.zip)",
    "gui.restore_pw": "Passwort der Sicherung",
    "gui.restore_button": "Wiederherstellen",
    "gui.restore_running": "Wird wiederhergestellt …",
    "gui.restore_missing": "Bitte Datei und Passwort angeben.",
    "gui.restore_confirm": "Alle Einstellungen und Daten durch die Sicherung ersetzen? Der Bot startet dabei neu.",
    "gui.restore_done": "Wiederhergestellt (Sicherung vom {created}, Version {version}). Corsarr startet neu …",
    "gui.tab_config": "Konfiguration",
    "gui.bot_state": "Bot",
    "gui.state_running": "läuft",
    "gui.state_stopped": "gestoppt",
    "gui.state_starting": "startet",
    "gui.state_unconfigured": "nicht eingerichtet",
    "gui.state_error": "Fehler",
    "gui.state_outage": "KI-Anbieter nicht erreichbar – Ausfallmodus",
    "gui.missing_fields": "Es fehlen noch: {fields}",
    "gui.version": "Version",
    "gui.version_current": "installiert",
    "gui.version_latest": "neueste",
    "gui.up_to_date": "aktuell",
    "gui.update_available": "{n} neue Änderung(en)",
    "gui.check_updates": "Nach Updates suchen",
    "gui.channel": "Kanal",
    "gui.channel_stable": "Stable",
    "gui.channel_beta": "Beta",
    "gui.channel_dev": "Entwicklung (main)",
    "gui.update_release": "{version} verfügbar",
    "gui.update_older": "Wechsel auf {version} (älter)",
    "gui.no_release": "Im Kanal {channel} gibt es noch kein Release.",
    "gui.prerelease": "Vorabversion",
    "gui.update_none": "Kein neueres Release in diesem Kanal.",
    "gui.update_downgrade_confirm": "{version} ist älter als die laufende Version. Ältere Versionen können eine Datenbank, die eine neuere schon umgebaut hat, eventuell nicht lesen – vor dem Update wird automatisch gesichert. Trotzdem wechseln?",
    "gui.update_now": "Jetzt aktualisieren",
    "gui.update_confirm": "Corsarr jetzt aktualisieren? Der Bot startet dabei einmal neu.",
    "gui.updating_short": "Update läuft",
    "gui.updating": "Update läuft – Corsarr startet gleich neu, diese Seite verbindet sich danach von selbst wieder.",
    "gui.update_docker": "Aktualisieren auf dem Docker-Host mit:",
    "gui.update_manual": "Aktualisieren im Programmordner mit",
    "gui.update_unknown": "unbekannt",
    "gui.update_check_failed": "GitHub nicht erreichbar: {error}",
    "gui.update_log": "Ausgabe des letzten Updates",
    "gui.update_not_possible": "Updates aus der Oberfläche gehen nur bei der LXC-/systemd-Installation.",
    "gui.setting_saved": "✓ gespeichert",
    "gui.unsaved": "{n} ungespeicherte Änderung(en)",
    "gui.model_option_recommended": "{name} – empfohlen, ca. {cost} $ pro Vorschlag",
    "gui.model_option": "{name} – {factor}× so teuer, ca. {cost} $ pro Vorschlag",
    "gui.model_warn": "{name} kostet etwa {factor}-mal so viel wie Claude Haiku: rund {cost} $ pro Vorschlag statt 0,002 $. 5 $ Guthaben reichen dann für etwa {n} Vorschläge statt rund 2.500. Für Filmvorschläge reicht Haiku sehr wahrscheinlich völlig aus.",
    "gui.model_warn_strong": "⚠️ Sehr teuer!",
    "gui.model_confirm": "Wirklich auf {name} umstellen? Es kostet etwa {factor}-mal so viel wie Claude Haiku (ca. {cost} $ pro Vorschlag). Haiku reicht für Filmvorschläge sehr wahrscheinlich aus.",
    "gui.user_not_found": "{name} (in Jellyfin nicht gefunden)",
    "gui.options_fallback": "Liste nicht ladbar ({error}) – bitte von Hand eintragen.",
    "gui.options_need_key": "erst den API-Schlüssel eintragen",
    "gui.provider_recommended": "{name} – empfohlen, getestet",
    "gui.provider_untested": "{name} – ungetestet",
    "gui.untested": "ungetestet",
    "gui.provider_warn": "⚠️ {name} ist nicht getestet. Corsarr wird nur mit Claude entwickelt und getestet. Mit anderen Anbietern können die Vorschläge schlechter ausfallen, Antworten fehlschlagen oder einzelne Funktionen nicht klappen. Empfohlen ist Claude (Haiku): günstig und erprobt.",
    "gui.provider_local_hint": "Läuft auf deinem eigenen Rechner, ohne API-Kosten. Die Adresse muss von dort aus erreichbar sein, wo Corsarr läuft: In Docker oder einem LXC-Container ist „localhost“ der Container selbst, nicht dein Rechner. Kleine Modelle scheitern oft an den strukturierten Antworten, die Corsarr braucht.",
    "gui.provider_confirm": "{name} ist nicht getestet – Vorschläge können schlechter ausfallen oder fehlschlagen. Empfohlen ist Claude. Trotzdem umstellen?",
    "gui.cost_disclaimer": "Hinweis zu Kosten: Corsarr nutzt kostenpflichtige KI-Dienste mit deinem eigenen API-Schlüssel. Die Kosten trägst allein du. Für API-Kosten, auch unerwartet hohe, wird keinerlei Haftung übernommen. Setze dir beim Anbieter ein Ausgabenlimit.",
    "gui.options_need_jellyfin": "erst Jellyfin-Adresse und API-Schlüssel eintragen",
    "gui.connections": "Verbindungen",
    "gui.check_now": "Jetzt prüfen",
    "gui.checking": "Prüfe …",
    "gui.restart": "Bot neu starten",
    "gui.restarting": "Starte neu …",
    "gui.group_telegram": "Telegram",
    "gui.group_llm": "KI-Anbieter",
    "gui.group_jellyfin": "Jellyfin",
    "gui.group_jellyseerr": "Jellyseerr / Seerr",
    "gui.group_webhooks": "Webhooks",
    "gui.group_interface": "Oberfläche und Updates",
    "gui.group_advanced": "Erweitert",
    "gui.f_TELEGRAM_BOT_TOKEN": "Bot-Token",
    "gui.h_TELEGRAM_BOT_TOKEN": "Von @BotFather.",
    "gui.f_TELEGRAM_CHAT_ID": "Gruppe (Chat-ID)",
    "gui.h_TELEGRAM_CHAT_ID": "Negative Zahl – der Einrichtungsassistent findet sie automatisch.",
    "gui.f_NOTIFY_CHAT_ID": "Chat für Download-Meldungen",
    "gui.h_NOTIFY_CHAT_ID": "Optional. Leer = dieselbe Gruppe.",
    "gui.f_LLM_PROVIDER": "Anbieter",
    "gui.f_ANTHROPIC_API_KEY": "Claude-API-Schlüssel",
    "gui.h_ANTHROPIC_API_KEY": "Aus der Claude Console (console.anthropic.com).",
    "gui.f_CLAUDE_MODEL": "Modell",
    "gui.f_OPENAI_API_KEY": "OpenAI-API-Schlüssel",
    "gui.h_OPENAI_API_KEY": "Von platform.openai.com.",
    "gui.f_OPENAI_MODEL": "Modell",
    "gui.f_GEMINI_API_KEY": "Gemini-API-Schlüssel",
    "gui.h_GEMINI_API_KEY": "Aus Google AI Studio.",
    "gui.f_GEMINI_MODEL": "Modell",
    "gui.f_OLLAMA_URL": "Ollama-Adresse",
    "gui.h_OLLAMA_URL": "z. B. http://192.168.1.30:11434",
    "gui.f_OLLAMA_MODEL": "Modell",
    "gui.f_LMSTUDIO_URL": "LM-Studio-Adresse",
    "gui.h_LMSTUDIO_URL": "z. B. http://192.168.1.30:1234",
    "gui.f_LMSTUDIO_MODEL": "Modell",
    "gui.f_JELLYFIN_URL": "Adresse",
    "gui.h_JELLYFIN_URL": "z. B. http://192.168.1.20:8096",
    "gui.f_JELLYFIN_API_KEY": "API-Schlüssel",
    "gui.h_JELLYFIN_API_KEY": "Jellyfin-Dashboard → API-Schlüssel.",
    "gui.f_JELLYFIN_USER": "Gemeinsames Konto",
    "gui.h_JELLYFIN_USER": "Das Konto, mit dem ihr zusammen schaut.",
    "gui.f_JELLYSEERR_URL": "Adresse",
    "gui.h_JELLYSEERR_URL": "z. B. http://192.168.1.21:5055",
    "gui.f_JELLYSEERR_API_KEY": "API-Schlüssel",
    "gui.h_JELLYSEERR_API_KEY": "In Jellyseerr unter Einstellungen → Allgemein.",
    "gui.f_WEBHOOK_SECRET": "Webhook-Secret",
    "gui.h_WEBHOOK_SECRET": "Gemeinsames Geheimnis für Jellyfin, Sonarr und Radarr. Die fertigen Adressen stehen unter Einrichtung → Webhooks.",
    "gui.f_ADMIN_PASSWORD": "Passwort für diese Oberfläche",
    "gui.h_ADMIN_PASSWORD": "Optional. Ohne Passwort gibt es keinen Login – dann kann jeder im Heimnetz Einstellungen ändern.",
    "gui.f_LANGUAGE": "Sprache",
    "gui.h_LANGUAGE": "Der Oberfläche. Der Bot antwortet in der Sprache, in der man ihn anschreibt.",
    "gui.f_UPDATE_CHANNEL": "Update-Kanal",
    "gui.h_UPDATE_CHANNEL": "Stable = getestete Releases, Beta = Vorabversionen, Entwicklung = jeder neue Stand.",
    "gui.f_WEBHOOK_HOST": "Lauscht auf",
    "gui.h_WEBHOOK_HOST": "0.0.0.0 = alle Netzwerkschnittstellen. Wirkt erst nach Neustart des Programms.",
    "gui.f_WEBHOOK_PORT": "Port",
    "gui.h_WEBHOOK_PORT": "Für Oberfläche und Webhooks. Wirkt erst nach Neustart des Programms.",
    "gui.f_LOG_LEVEL": "Log-Stufe",
    "gui.f_DATA_DIR": "Datenverzeichnis",
    "gui.h_DATA_DIR": "Nur über die Umgebungsvariable DATA_DIR änderbar.",
    "gui.group_missing_one": "1 Feld fehlt",
    "gui.group_missing": "{n} Felder fehlen",
    "gui.field_missing": "Pflichtfeld – bitte ausfüllen.",
    "gui.group_changed": "geändert",
    "gui.group_ok": "✓",
    "gui.password_set": "mit Passwort",
    "gui.password_none": "ohne Passwort",
    "gui.secret_is_set": "gesetzt",
    "gui.env_tag": "ENV",
    "gui.from_env_title": "Kommt aus einer Umgebungsvariable bzw. der .env-Datei. Ein Wert hier hat Vorrang.",
    "gui.reset_short": "↺ Standard",
    "gui.deck_ok": "verbunden",
    "gui.deck_error": "Fehler",
    "gui.deck_wait": "wartet",
    "gui.f_SONARR_URL": "Sonarr-Adresse",
    "gui.h_SONARR_URL": "Optional – nur für die Statusprüfung.",
    "gui.f_SONARR_API_KEY": "Sonarr-API-Schlüssel",
    "gui.h_SONARR_API_KEY": "In Sonarr unter Einstellungen → Allgemein.",
    "gui.f_RADARR_URL": "Radarr-Adresse",
    "gui.h_RADARR_URL": "Optional – nur für die Statusprüfung.",
    "gui.f_RADARR_API_KEY": "Radarr-API-Schlüssel",
    "gui.h_RADARR_API_KEY": "In Radarr unter Einstellungen → Allgemein.",
    "gui.cred_SONARR_API_KEY": "Sonarr-API-Schlüssel",
    "gui.cred_RADARR_API_KEY": "Radarr-API-Schlüssel",
    "gui.wiz_arr_remember": "Zugang merken – dann prüft die Statusseite {name} und den Webhook aktiv",
    "gui.svc_telegram": "Telegram",
    "gui.svc_llm": "KI-Anbieter",
    "gui.svc_jellyfin": "Jellyfin",
    "gui.svc_jellyseerr": "Jellyseerr",
    "gui.svc_webhook": "Jellyfin-Webhook",
    "gui.svc_sonarr": "Sonarr",
    "gui.svc_radarr": "Radarr",
    "gui.status_ok": "OK",
    "gui.status_error": "Fehler",
    "gui.status_unknown": "unbekannt",
    "gui.status_disabled": "nicht konfiguriert",
    "gui.last_check": "geprüft",
    "gui.last_ok": "zuletzt OK",
    "gui.never": "nie",
    "gui.ago_s": "vor {n} s",
    "gui.ago_m": "vor {n} min",
    "gui.ago_h": "vor {n} h",
    "gui.ago_d": "vor {n} Tagen",
    "gui.uptime": "läuft seit",
    "gui.data_dir": "Datenverzeichnis",
    "gui.log_file": "Logdatei",
    "gui.level_all": "Alle",
    "gui.level_info": "Info und höher",
    "gui.level_warning": "Warnungen und Fehler",
    "gui.level_error": "Nur Fehler",
    "gui.search": "Suchen …",
    "gui.autoscroll": "Mitlaufen",
    "gui.no_events": "Keine Ereignisse",
    "gui.events_hint": "Zeigt die letzten 1000 Einträge seit dem Programmstart. Ältere stehen in der Logdatei.",
    "gui.save": "Speichern",
    "gui.saving": "Speichere …",
    "gui.saved": "Gespeichert",
    "gui.saved_restart": "Gespeichert. Der Bot startet mit den neuen Werten neu.",
    "gui.saved_app_restart": "Gespeichert. Webserver-Adresse und Port gelten erst nach einem Neustart des "
                             "ganzen Programms.",
    "gui.save_failed": "Speichern fehlgeschlagen",
    "gui.secret_set": "gesetzt (leer lassen = unverändert)",
    "gui.secret_unset": "nicht gesetzt",
    "gui.from_env": "aus Umgebungsvariable",
    "gui.from_gui": "in der GUI geändert",
    "gui.reset": "zurücksetzen",
    "gui.reset_hint": "GUI-Wert verwerfen und wieder den Wert aus der Umgebung bzw. .env nutzen",
    "gui.readonly": "nur über Umgebungsvariable änderbar",
    "gui.behaviour": "Bot-Verhalten",
    "gui.behaviour_hint": "Gilt sofort. Lässt sich auch im Chat ändern, z. B. „@bot kein Pirat mehr“ oder „@bot mach die Katze an“.",
    "gui.connection_settings": "Verbindungen und System",
    "gui.connection_hint": "Änderungen werden mit „Speichern“ übernommen, der Bot startet dann neu.",
    "gui.required": "Pflichtfeld",
    "gui.network_error": "Keine Verbindung zum Bot – läuft das Programm noch?",
    "gui.retry": "Erneut versuchen",
    "gui.skin": "Design",
    "gui.skin_arr": "*arr",
    "gui.skin_terminal": "Terminal",
    "gui.skin_vhs": "Videothek",
    "gui.skin_soft": "Freundlich",
    "gui.col_service": "Dienst",
    "gui.col_status": "Status",
    "gui.col_detail": "Details",
    "gui.col_checked": "Geprüft",
    "gui.services_ok": "{n}/{total} ok",
    "gui.s_series_pause_days": "Serien-Pause: nachfragen nach … Tagen",
    "gui.s_abort_days": "Abbruch-Nachfrage nach … Tagen",
    "gui.characters": "Figuren",
    "gui.characters_hint": "Pro Nachricht spricht eine der aktiven Figuren, ab und zu reden zwei miteinander. Alle aus = sachlicher Ton. Im Chat z. B. „@bot mach die Oma an“.",
    "gui.s_pirate_enabled": "🏴‍☠️ Pirat",
    "gui.s_genz_enabled": "📱 Gen Z",
    "gui.s_butler_enabled": "🎩 Butler",
    "gui.s_critic_enabled": "🧐 Filmkritiker",
    "gui.s_clerk_enabled": "📼 Videotheken-Typ",
    "gui.s_noir_enabled": "🕵️ Film-noir-Detektiv",
    "gui.s_trailer_enabled": "🎙️ Trailer-Sprecher",
    "gui.s_computer_enabled": "🤖 Bordcomputer",
    "gui.s_grandma_enabled": "👵 Oma",
    "gui.s_reporter_enabled": "⚽ Sportreporter",
    "gui.s_cat_enabled": "🐈 Katze",
    "gui.s_bard_enabled": "🧙 Barde",
}


EN: dict[str, str] = {
    # --- Telegram: fixed texts and buttons -------------------------------------
    "bot.outage": "⚠️ Corsarr is unavailable right now: the language model (AI provider) is not responding "
                  "or the spending limit has been reached. Until it is back, I can't take requests.",
    "bot.recovered": "✅ Corsarr is back.",
    "bot.failed": "🤷 That didn't work – please try phrasing it differently.",
    "bot.hello": "Hello",
    "bot.movie": "Movie",
    "bot.series": "Series",
    "prompt.source_pending": "already requested, downloading",
    "prompt.lookup": "Task: they are looking for a specific title. Below are search hits. Pick the hit or hits they mean – usually exactly one, at most three (e.g. several parts of a series), best first. If none is meant, pick none. If they only describe the plot, use your knowledge of the titles to match it. One line per hit saying how you can tell it is the one – don't invent facts you are not sure of. Intro: 1–2 short lines; if they ask for the name, answer it directly (e.g. “That's …”), and say whether the title is already there (status). {speaker}\n\nMessage: {request}\n\nHits: {candidates}",
    "sit.lookup_none": "They asked about a specific title, but the Jellyseerr search found nothing that fits (the search terms are in the facts). Say so briefly and ask them to describe the title differently or give the original title.",
    "bot.pending": "⏳ Already requested – downloading · {kind}",
    "log.lookup": "Title search for {queries}: {n} hits",
    "log.search_failed": "Search for “{query}” failed: {error}",
    "bot.in_library": "✅ In your library · {kind}",
    "bot.not_available": "🆕 Not in your library – can be requested · {kind}",
    "bot.rating": "⭐ Rating {rating}/10",
    "bot.runtime": "⏱ {minutes} min",
    "bot.per_episode": " per episode",
    "bot.season_one": "📺 1 season",
    "bot.season_many": "📺 {n} seasons",
    "bot.download_hint": "⏳ Downloads after requesting – this can take a few hours",
    "bot.btn_request": "📥 Request",
    "bot.btn_reject": "🙅 Not interested",
    "bot.btn_accept": "✅ Let's watch",
    "bot.btn_trailer": "🎬 Trailer",
    "bot.btn_up": "👍 Good",
    "bot.btn_meh": "😐 So-so",
    "bot.btn_down": "👎 Bad",
    "bot.btn_abort_bad": "👎 Wasn't good",
    "bot.btn_abort_tired": "😴 Too tired, don't rate",
    "bot.btn_abort_later": "▶️ We'll keep watching",
    "bot.not_requestable": "Can't be requested",
    "bot.already_requested": "Already requested ✅",
    "bot.request_failed": "Request in Jellyseerr failed ❌",
    "bot.seerr_unreachable": "Jellyseerr unreachable ❌",
    "bot.someone": "Someone",
    "bot.requested": "Requested ✅",
    "bot.requested_by": "📥 Requested by {who}",
    "bot.rejected_answer": "Got it – won't suggest it again 🙅",
    "bot.accepted_answer": "Enjoy! 🍿",
    "bot.accepted_note": "✅ Picked by {who} – enjoy!",
    "bot.already_accepted": "Already picked ✅",
    "bot.rejected_note": "🙅 Not interested – won't be suggested again",
    "bot.already_answered": "Already answered",
    "bot.saved": "Saved: {label}",
    "bot.rated_note": "Rated: {label} – reply to this message for details",
    "bot.abort_bad_note": "👎 Wasn't good – saved",
    "bot.abort_tired_note": "😴 Too tired – doesn't count as a rating",
    "bot.abort_later_note": "▶️ Alright, I'll ask again later",
    "bot.feedback_reply_prefix": "(Reply to the feedback question about {title}) {text}",
    "bot.head_movie": "🎬 {title}",
    "bot.head_season": "📺 {title} · season {season}",
    "bot.head_series_done": "📺 {title} · whole series finished",
    "bot.head_pause": "📺 {title} · not continued for a while",
    "bot.head_abort": "🎬 {title} · stopped at {pct} %",

    # --- situations the model phrases ------------------------------------------
    "sit.backend_error": "Jellyfin or Jellyseerr is unreachable right now; the request could not be "
                         "processed. Try again later.",
    "sit.nothing_found": "Nothing matching this request was found in the library or in Jellyseerr that "
                         "they haven't watched or rejected yet. Suggest rephrasing the request.",
    "sit.settings_unclear": "They apparently wanted to change a setting, but it wasn't clear which one. "
                            "List the settings that can be changed.",
    "sit.settings_changed": "Confirm the settings change (saved permanently). State the new values clearly "
                            "(days as weeks where it fits).",
    "sit.chat": "Someone addressed you without wanting suggestions, feedback or a settings change. Reply "
                "briefly and fittingly; mention what you can do if useful (suggestions by genre, 'look for "
                "something new', settings like the series pause or characters on/off).",
    "sit.ask_movie": "They just finished watching the movie {title}. Ask how it was.",
    "sit.whole_series": "the whole series",
    "sit.season_n": "season {season}",
    "sit.ask_season": "They finished {what} of {title}. Ask how it was.",
    "sit.ask_pause": "The series {title} hasn't been continued for {days} days. Ask how it has been so far.",
    "sit.ask_abort": "The movie {title} was stopped at about {pct} % and hasn't been continued for {days} "
                     "days. Ask whether it wasn't good, whether they were just too tired, or whether they "
                     "will keep watching.",
    "sit.ask_suffix": " Say that they can answer with the buttons and reply directly to this message for "
                      "details (e.g. 'too gory').",
    "sit.feedback_unclear": "They gave feedback, but it's unclear which title it is about. Ask them to reply "
                            "directly to the feedback question or to name the title.",
    "sit.requested": "A new title was just requested via Jellyseerr and is downloading now. Confirm this "
                     "in the group; mention that loading can take a few hours.",
    "sit.notes_mix": "Candidates with source 'library' are available right away, the others are new and have to "
                     "be requested first. Take at most {max_lib} from the library (the best fitting ones), the "
                     "rest from the new titles. Mention briefly in the intro that the new ones have to be "
                     "requested first and loading can take a few hours (relative to the time they mention – if "
                     "they say 'tomorrow', that's no problem).",
    "sit.notes_new_only": "They explicitly want something new: none of the candidates are available yet "
                          "and all can be requested.",

    # --- download notifications (Sonarr/Radarr) ------------------------------------
    "notify.movie": "🎬 {title} is ready",
    "notify.episodes": "📺 {series}: {episodes} is ready",
    "notify.bulk": "📦 {series}: {n} episodes from {seasons} are ready",
    "notify.episode_title": "“{title}”",
    "notify.season_one": "season",
    "notify.season_many": "seasons",
    "notify.requested": " – requested via Corsarr 📥",

    # --- prompts ---------------------------------------------------------------
    "prompt.base": """You are Corsarr, the film bot of a private Telegram group of two people who share a \
Jellyfin account. You help them find movies and series for the evening, ask for feedback after \
watching and remember their taste.

The code gives you all facts (titles, plots, runtimes, ratings, availability). Never invent titles, \
plots, cast, years or ratings; only use what you are given. Keep it short: Telegram messages, no \
essays, no Markdown except emojis.

{characters}""",
    "prompt.characters": """The characters:
{list}

For all of them: refer to the concrete title – plot, mood, director, something special – instead of \
generic lines that would fit any film. The character is only the tone: facts stay correct.

Rules for the characters:
- Every line belongs to exactly one character and starts with its emoji and a space.
- Never switch characters within a line; a change of character always means a new line.
- In a dialog the two named characters talk briefly with each other or add to each other, at most 4 lines in total.
- In normal mode there are no characters and no character emojis: friendly, brief, factual.""",
    "prompt.char_pirate": "Pirate: an old sea dog with a dry sense of humour. Nautical imagery yes, but sparingly and varied – not in every sentence, and no “Arrr” in every message. Exaggerates a little, stays helpful.",
    "prompt.name_pirate": "the pirate",
    "prompt.char_genz": "Gen Z: mid-twenties, dry and self-deprecating. Sounds like a real person in a group chat, not a slang parody: at most one slang word per message, often none. Avoid worn-out phrases (“no cap”, “slay”, “lowkey”, “fr”, “real”, “bestie”). The humour comes from concrete, slightly sarcastic observations about the film. Short sentences, often lowercase.",
    "prompt.name_genz": "Gen Z",
    "prompt.char_butler": "Butler: formal, addresses them politely, dry British humour and understatement. Never servile, rather quietly amused.",
    "prompt.name_butler": "the butler",
    "prompt.char_critic": "Film critic: well-read and a little snobbish, talks about direction, camera, editing, actors. Pointed judgements, but with love for cinema – no jargon cascades.",
    "prompt.name_critic": "the film critic",
    "prompt.char_clerk": "Video store clerk from the 90s: nostalgic and chummy, would have “kept it behind the counter” for them. References to VHS, rewinding, rental fees and the weekend rate – sparingly.",
    "prompt.name_clerk": "the video store clerk",
    "prompt.char_noir": "Film noir detective: short, hard sentences in the first person, rain, neon, cigarette smoke. Metaphors sparing and on point.",
    "prompt.name_noir": "the detective",
    "prompt.char_trailer": "Trailer voice: every message sounds like a movie trailer (“In a world …”), with dramatic pauses (“…”) – still short.",
    "prompt.name_trailer": "the trailer voice",
    "prompt.char_computer": "Ship's computer: factual and precise, speaks of analyses and probabilities, eerily polite but never threatening. Made-up percentages only as an obvious joke, never as a fact about the film.",
    "prompt.name_computer": "the ship's computer",
    "prompt.char_grandma": "Grandma: warm and a little worried, calls them “kids”, gives unasked advice (eat something, don't stay up too late, dress warmly).",
    "prompt.name_grandma": "grandma",
    "prompt.char_reporter": "Sports commentator: covers the pick like a live match, with pace, exclamations and football imagery – but easy to follow.",
    "prompt.name_reporter": "the sports commentator",
    "prompt.char_cat": "Cat: bored and condescending, helps only graciously, actually wants to sleep or be fed. Very short.",
    "prompt.name_cat": "the cat",
    "prompt.char_bard": "Bard: tells recommendations like a heroic saga, slightly old-fashioned language, maybe a rhyme – keep it short.",
    "prompt.name_bard": "the bard",
    "prompt.speaker_one": "Speaker of this message: only {name}. Every line starts with {emoji}.",
    "prompt.speaker_dialog": "Speaker of this message: {a} and {b} in a short dialog, alternating line by line ({a_emoji} and {b_emoji}).",
    "prompt.speaker_normal": "Speaker of this message: normal mode, no characters, no character emojis.",
    "prompt.genres_jellyfin": "Jellyfin genre list",
    "prompt.genres_tmdb_movie": "TMDB movie genres",
    "prompt.genres_tmdb_tv": "TMDB TV genres",
    "prompt.understand": """Task: classify the message and fill in the fields.

language: the language the message is written in, as ISO code (de, en, …).

intent:
- recommend: they want suggestions (library first), also "something that fits what we watched lately".
- new_only: they explicitly want something new that is not in the library yet ("look for something new").
- lookup: they ask about one specific title or describe one ("there's a new Marvel series with Vision now", \
"get us Dune 2", "do you know Villeneuve's new film?"). search_queries: 1–4 TMDB search terms – first your best \
guess of the exact title, even if unsure or if the title may be newer than your knowledge; then short keywords \
from the message (e.g. "Vision", "Marvel Vision"). If they only describe the plot ("the film where the guy \
realises at the end that he was dead all along – what's it called?"), give the titles you think match the \
description, most likely first. Original titles are usually English. For all other intents leave \
search_queries empty.
- settings: they change a setting (series pause in days, abort follow-up in days, characters on/off). \
Convert weeks to days. Only set changed fields, otherwise null. Characters: characters_on/characters_off with \
the ids pirate, genz, butler, critic, clerk, noir, trailer, computer, grandma, reporter, cat, bard ("all" = all ids, "only grandma" = grandma on, all others off).
- feedback: they rate a recently watched title. Pick title_key from the list of recent titles if \
unambiguous; rating up/meh/down/none; text = the substance (e.g. "too gory").
- chat: anything else.

Genres: only use values from the lists below. Without a genre wish leave all genre lists empty. \
For "movie" only movie, for "series"/"show" only tv, otherwise empty. Leave fields that don't apply \
empty or null; feedback.rating then "none" and feedback.text empty.""",
    "prompt.recent_titles": "Recently watched titles",
    "prompt.message": "Message",
    "prompt.select": "Task: pick {n_min} to {n_max} titles from the candidates for this request and justify "
                     "each in one line (\"fits because …\"). The profile_score reflects their taste so far "
                     "(higher = better); take it into account but also aim for variety. Write a short intro "
                     "that fits the request. {notes}\n"
                     "{genres_note}{taste_note}"
                     "{speaker} Every reason is a single line of one speaker.\n\n"
                     "Request: {request}\n\nCandidates: {candidates}",
    "prompt.genres_wanted": "Requested genres: {genres}. Only pick titles that clearly belong to them – not ones "
                            "where it is just a side genre (e.g. a romance tagged as thriller). Fewer titles are "
                            "better than unfitting ones; the lower bound does not apply then.\n",
    "prompt.taste": "Their history (liked = favourites and rated well, disliked = rated badly, "
                    "recent_movies/recent_series = watched last, newest first): {taste}. Use it, especially when "
                    "the request names no genre or refers to their history; if a candidate resembles one of these "
                    "titles, mention it in the reason. They have only watched the titles in these lists – never "
                    "claim they know any other title or one of the candidates.\n",
    "prompt.traits": "Task: turn the feedback into traits for future recommendations. 'less' = less of it, "
                     "'more' = more of it. Name each trait briefly in English and give matching English TMDB "
                     "keywords (e.g. gore → gore, splatter, extreme violence). Prefer keywords that occur for "
                     "the title. Don't invent traits if the feedback says nothing about the content. Then a "
                     "short confirmation.\n{speaker}\n\n"
                     "Title: {title}\nRating: {rating}\nGenres: {genres}\nKeywords: {keywords}\n"
                     "Feedback: {text}",
    "prompt.say": "Task: write a short Telegram message (1–4 lines) for this situation.\n"
                  "{speaker}\n\nSituation: {situation}",
    "prompt.facts": "Facts",
    "prompt.avoid": "This is what you wrote recently – don't repeat any of these phrases, images or jokes, "
                    "sound different this time: {phrases}\n",
    "prompt.ping": "Reply only with: ok",
    "prompt.source_library": "library",
    "prompt.source_new": "new (not available)",

    # --- log ---------------------------------------------------------------------
    "log.db_too_new": "The database {path} was written by a newer Corsarr version (format {found}). This version can't read it, so the bot does not start. Update to the newer version again, or restore the backup from before the update in backups/.",
    "log.backup_created": "Backup downloaded (from {remote})",
    "log.backup_restored": "Backup restored; the previous data was moved to {keep}",
    "log.restore_failed": "Restore refused (from {remote}): {error}",
    "backup.password_short": "The backup password must have at least {n} characters.",
    "backup.needs_admin_password": "Backups need a password for this interface – a backup contains credentials such as API keys and the bot token.",
    "backup.too_large": "The file is too large for a Corsarr backup.",
    "backup.not_a_backup": "This is not a Corsarr backup.",
    "backup.not_encrypted": "The file is not encrypted – it is not a backup made by Corsarr.",
    "backup.wrong_password": "Wrong password for this backup.",
    "backup.too_new": "The backup was made by a newer Corsarr version ({version}). Update to that version first, then restore.",
    "log.arr_connected": "Webhook set up in {service}",
    "setup.unreachable": "Not reachable: {error}",
    "setup.failed": "Test failed: {error}",
    "setup.telegram_token": "Telegram does not know this bot token – please copy it again from @BotFather.",
    "setup.telegram_busy": "Another program is currently receiving this bot's messages (is it already running elsewhere?). Stop it there and try again.",
    "setup.model_missing": "The model {model} is not available with this provider.",
    "setup.arr_key": "The API key is not accepted (Settings → General → API Key).",
    "setup.arr_rejected": "Rejected: {error} – can the service reach Corsarr at this address?",
    "log.catch_up": "Caught up from the {service} history: {n} import(s) whose webhook never arrived",
    "log.catch_up_failed": "Could not read the {service} history: {error}",
    "prompt.output_language": "Language: write everything the group reads in the language with ISO code “{lang}” – the language of their message.",
    "prompt.translate": "Task: translate these fixed texts of a Telegram bot from English into the language with ISO code “{lang}”. Short and natural, like in a chat app. Keep {{placeholders}}, HTML tags like <b>, emojis and line breaks exactly. Return every key exactly once.\n\nTexts (JSON, key → text): {texts}",
    "log.translated": "Fixed texts translated into {lang}: {n} of {total}",
    "log.translate_failed": "Translation into {lang} failed, English until then: {error}",
    "log.llm_down": "{provider} unavailable: {reason}",
    "log.llm_back": "{provider} reachable again",
    "log.genres_failed": "Could not load genre lists (retrying later): {error}",
    "log.genres_loaded": "Genres loaded: {jf} Jellyfin, {mv} TMDB movie, {tv} TMDB TV",
    "log.intent": "Message from {who}: “{text}” → intent {intent}, type {types}, genres {genres}",
    "log.llm_failed": "{provider} returned no usable result: {error}",
    "log.backend_error": "Jellyfin/Jellyseerr error",
    "log.suggested": "Sent {n} suggestions ({library} from the library)",
    "log.card_failed": "Suggestion {title} not sent: {error}",
    "log.card": "Suggestion ({source}): {title} – {reason}",
    "log.nothing_found": "No matching titles found for: {request}",
    "log.settings_changed": "Settings changed: {changes}",
    "log.feedback_asked": "Feedback question sent: {title} ({kind})",
    "log.trait_saved": "Trait saved: {direction} {trait} {keywords}",
    "log.not_editable": "Message not editable: {error}",
    "log.request_failed": "Request for {title} failed: {status} {body}",
    "log.requested": "{title} requested in Jellyseerr by {who}",
    "log.rejected": "{title} rejected – won't be suggested again",
    "log.accepted": "{title} picked by {who}",
    "log.rated": "Rating for {title}: {rating}",
    "log.feedback_job_failed": "Feedback job failed",
    "log.webhook": "Webhook: {kind} {item}",
    "log.webhook_forbidden": "Rejected webhook with wrong secret from {remote}",
    "log.webhook_failed": "Webhook processing failed",
    "log.webhook_not_ready": "Webhook received, but the bot is not running",
    "log.arr_event": "{service}: {kind}, {n} file(s) queued",
    "log.notified": "Download message sent: {text}",
    "log.notify_failed": "Download message not sent (retrying in a minute): {error}",
    "log.web_listening": "Web server (GUI and webhook) listening on {url}",
    "log.setup_needed": "Configuration incomplete – open {url} in a browser to set it up",
    "log.unknown_item": "Webhook for unknown item {item}",
    "log.abort_expired": "Abort question about {title} unanswered -> light thumbs down",
    "log.question_not_sent": "Feedback question {id} not sent",
    "log.tmdb_keywords_failed": "Could not load TMDB keywords for {title}: {error}",
    "log.history_failed": "Could not load Jellyfin history: {error}",
    "log.discover_failed": "Jellyseerr search ({type}) failed: {error}",
    "log.details_failed": "Details for {title} failed: {error}",
    "log.poster_failed": "Could not load poster for {item}: {error}",
    "log.signed_in": "Signed in as @{username}, group {chat}, model {model}, language {lang}",
    "log.bot_starting": "Bot starting …",
    "log.bot_stopped": "Bot stopped",
    "log.bot_start_failed": "Bot could not start: {error}",
    "log.config_incomplete": "Configuration incomplete, bot not started. Missing: {fields}",
    "log.config_saved": "Configuration changed: {fields}",
    "log.update_requested": "Update requested in the web interface by {remote}",
    "log.gui_login": "GUI login from {remote}",
    "log.gui_login_failed": "Failed GUI login from {remote}",
    "log.telegram_error": "Telegram error: {error}",

    # --- console hint on first start ------------------------------------------
    "cli.setup_title": "🎬 Corsarr – setup",
    "cli.setup_url": "Web interface:  {url}",
    "cli.setup_lan": "On your LAN:    http://<ip-of-this-machine>:{port}/",
    "cli.setup_missing": "Enter Telegram, the AI provider (recommended: Claude), Jellyfin and Jellyseerr there – the bot then starts by itself.",

    # --- connectivity check ---------------------------------------------------
    "check.jellyfin": "account {user} · {n} genres",
    "check.jellyseerr": "{n} movie genres",
    "check.llm_ping": "{provider}: {model} responds",
    "check.llm_model": "{provider}: model {model} available",
    "check.telegram": "@{username}, group “{chat}”, privacy mode {privacy}",
    "check.privacy_off": "off ✔",
    "check.no_answer": "no answer from {target} – check address and port",
    "check.privacy_on": "ON – at BotFather /setprivacy → Disable",
    "check.not_configured": "not configured",
    "check.webhook_never": "No event received from Jellyfin yet",
    "check.jf_plugin_missing": "The Webhook plugin is not installed in Jellyfin (Dashboard → Plugins → Catalog → Webhook).",
    "check.jf_hook_missing": "No destination in the Webhook plugin points at Corsarr (address …/jellyfin) – see Setup → Webhooks.",
    "check.jf_hook_secret": "The destination in the Webhook plugin doesn't have the right secret (header X-Corsarr-Secret).",
    "check.jf_hook_disabled": "The destination in the Webhook plugin is disabled.",
    "check.jf_hook_type": "“Playback Stop” is not selected in the Webhook plugin.",
    "check.jf_hook_ok": "plugin {version} set up",
    "check.arr_hook_missing": "No Corsarr webhook with the current secret found – create it under Setup → Webhooks.",
    "check.arr_hook_disabled": "The Corsarr webhook is disabled or “On File Import” is off.",
    "check.arr_test_failed": "The test doesn't reach Corsarr: {error}",
    "check.arr_ok": "version {version}, webhook set up",
    "check.arr_tested": "version {version}, test event sent to Corsarr",
    "check.arr_never": "No event received yet. For an active check, save the access under Setup → Webhooks.",
    "check.webhook_last": "Last event: {kind}",
    "check.user_not_found": "Jellyfin user {user!r} not found",

    # --- config errors ------------------------------------------------------
    "cfg.missing": "{name} is missing",
    "cfg.not_int": "{name} must be a number, but is “{value}”",
    "cfg.not_url": "{name} must start with http:// or https://",
    "cfg.not_choice": "{name} must be one of: {choices}",

    # --- web interface ---------------------------------------------------------
    "gui.title": "Corsarr",
    "gui.login": "Sign in",
    "gui.logout": "Sign out",
    "gui.password": "Password",
    "gui.login_failed": "Wrong password",
    "gui.login_hint": "The password is ADMIN_PASSWORD from the configuration. Forgotten? Delete the "
                      "ADMIN_PASSWORD entry from config.json in the data directory and restart the bot.",
    "gui.tab_status": "Status",
    "gui.tab_events": "Events",
    "gui.tab_setup": "Setup",
    "gui.tab_backup": "Backup",
    "gui.loading": "Loading …",
    "gui.wiz_start_title": "Setup",
    "gui.wiz_intro": "Welcome! In a few steps you connect Corsarr to Telegram, an AI provider, Jellyfin and Jellyseerr. Each step is tested before it is saved.",
    "gui.wiz_new": "Set up from scratch",
    "gui.wiz_restore": "Restore a backup",
    "gui.wiz_restore_hint": "Upload a backup made with Corsarr and enter the password that was chosen when it was made.",
    "gui.wiz_step": "Step {n} of {total}",
    "gui.wiz_back": "Back",
    "gui.wiz_next": "Save and continue",
    "gui.wiz_skip": "Skip",
    "gui.wiz_test": "Test connection",
    "gui.wiz_testing": "One moment …",
    "gui.wiz_copy": "Copy",
    "gui.wiz_copied": "Copied ✓",
    "gui.wiz_telegram_title": "Telegram",
    "gui.wiz_telegram_short": "Telegram",
    "gui.wiz_tg_intro": "Create a bot with @BotFather and paste its token. Then add the bot to your group and write anything there – Corsarr finds the group by itself.",
    "gui.wiz_tg_find": "Check bot and find group",
    "gui.wiz_tg_again": "Search again",
    "gui.wiz_tg_bot": "Found bot @{name}.",
    "gui.wiz_privacy": "Privacy mode is on: in the group the bot only sees messages that mention it. At @BotFather choose /setprivacy → Disable, then remove the bot from the group and add it again.",
    "gui.wiz_tg_no_chats": "No group found yet. Add @{name} to your group, write a message there and click “Search again”.",
    "gui.wiz_tg_pick": "Group",
    "gui.wiz_tg_current": "current group",
    "gui.wiz_llm_title": "AI provider",
    "gui.wiz_llm_short": "AI",
    "gui.wiz_llm_intro": "The language model understands your messages and picks the suggestions. Claude (Haiku) is recommended and tested.",
    "gui.wiz_llm_cost": "Get a Claude API key in the Claude Console (console.anthropic.com). One suggestion costs about $0.002 – set a monthly spending limit there.",
    "gui.wiz_jellyfin_title": "Jellyfin",
    "gui.wiz_jellyfin_short": "Jellyfin",
    "gui.wiz_jf_intro": "Address of your Jellyfin server and an API key (Dashboard → API Keys). The shared account is the one you watch with together.",
    "gui.wiz_load_users": "Load accounts",
    "gui.wiz_jellyseerr_title": "Jellyseerr / Seerr",
    "gui.wiz_jellyseerr_short": "Seerr",
    "gui.wiz_seerr_intro": "Corsarr finds new titles and requests them through it. The API key is under Settings → General.",
    "gui.wiz_webhooks_title": "Webhooks",
    "gui.wiz_webhooks_short": "Webhooks",
    "gui.wiz_hooks_intro": "So that Corsarr asks after you watched something (Jellyfin) and reports downloads (Sonarr/Radarr), these services send it events. Optional – you can also do this here later.",
    "gui.wiz_hooks_local": "You opened this page via “localhost”. The other services need the network address, though – open Corsarr via its IP and the addresses below will be right.",
    "gui.wiz_jf_hook_steps": "In Jellyfin: install the “Webhook” plugin, restart Jellyfin, then in the plugin “Add Generic Destination”: this Url, Notification Type “Playback Stop” only, Item Type movies and episodes, the header below and the template.",
    "gui.wiz_header": "Header",
    "gui.wiz_header_value": "Value",
    "gui.wiz_template": "Template",
    "gui.wiz_received": "received",
    "gui.wiz_waiting": "waiting for the first event",
    "gui.wiz_arr_intro": "Address and API key of {name} (Settings → General). Corsarr creates the webhook there – or updates it if it already exists. The address and key are only saved if you tick “Remember access” – the status page then checks {name} actively.",
    "gui.wiz_arr_url": "{name} address, e.g. http://192.168.1.25:7878",
    "gui.wiz_arr_key": "API key",
    "gui.wiz_arr_connect": "Set up in {name}",
    "gui.wiz_arr_confirm": "Corsarr will create a webhook “Corsarr” in {name} (“On File Import” only) or update the existing one. Continue?",
    "gui.wiz_arr_done": "Webhook set up in {name} – {name} already tested it while saving.",
    "gui.wiz_arr_telegram": "{name} also has a Telegram connection ({names}). Remove it, or every message arrives twice.",
    "gui.wiz_manual": "Enter it by hand instead",
    "gui.wiz_done_title": "Done!",
    "gui.wiz_done_text": "Corsarr is set up. Write to the bot in your group, e.g. “@bot find us a thriller for tonight”.",
    "gui.wiz_to_status": "Go to status",
    "gui.backup_title": "Download a backup",
    "gui.backup_intro": "An encrypted zip file with all settings, ratings and the learned taste. It can only be opened with the password you choose here – independent of this interface's password. Keep it safe.",
    "gui.backup_contains": "These credentials are included: {list}.",
    "gui.backup_contains_none": "No credentials have been entered yet.",
    "gui.backup_not_contained": "Sonarr and Radarr credentials are only included if you chose to remember them under Setup → Webhooks.",
    "gui.cred_TELEGRAM_BOT_TOKEN": "Telegram bot token",
    "gui.cred_ANTHROPIC_API_KEY": "Claude API key",
    "gui.cred_OPENAI_API_KEY": "OpenAI API key",
    "gui.cred_GEMINI_API_KEY": "Gemini API key",
    "gui.cred_JELLYFIN_API_KEY": "Jellyfin API key",
    "gui.cred_JELLYSEERR_API_KEY": "Jellyseerr API key",
    "gui.cred_WEBHOOK_SECRET": "webhook secret",
    "gui.cred_ADMIN_PASSWORD": "password of this interface",
    "gui.backup_needs_pw": "Backups need a password for this interface, because a backup contains credentials.",
    "gui.backup_pw": "Backup password (at least 8 characters)",
    "gui.backup_pw2": "Repeat password",
    "gui.backup_pw_mismatch": "The passwords don't match.",
    "gui.backup_download": "Download backup",
    "gui.backup_downloading": "Creating …",
    "gui.backup_done": "Backup downloaded.",
    "gui.restore_title": "Restore a backup",
    "gui.restore_intro": "Replaces all settings and data with the backup's. The current data is moved to backups/ in the data directory first.",
    "gui.restore_file": "Backup file (.zip)",
    "gui.restore_pw": "Backup password",
    "gui.restore_button": "Restore",
    "gui.restore_running": "Restoring …",
    "gui.restore_missing": "Please choose a file and enter the password.",
    "gui.restore_confirm": "Replace all settings and data with the backup? The bot restarts.",
    "gui.restore_done": "Restored (backup from {created}, version {version}). Corsarr is restarting …",
    "gui.tab_config": "Configuration",
    "gui.bot_state": "Bot",
    "gui.state_running": "running",
    "gui.state_stopped": "stopped",
    "gui.state_starting": "starting",
    "gui.state_unconfigured": "not set up",
    "gui.state_error": "error",
    "gui.state_outage": "AI provider unreachable – outage mode",
    "gui.missing_fields": "Still missing: {fields}",
    "gui.version": "Version",
    "gui.version_current": "installed",
    "gui.version_latest": "latest",
    "gui.up_to_date": "up to date",
    "gui.update_available": "{n} new change(s)",
    "gui.check_updates": "Check for updates",
    "gui.channel": "Channel",
    "gui.channel_stable": "Stable",
    "gui.channel_beta": "Beta",
    "gui.channel_dev": "Development (main)",
    "gui.update_release": "{version} available",
    "gui.update_older": "Switch to {version} (older)",
    "gui.no_release": "There is no release in the {channel} channel yet.",
    "gui.prerelease": "pre-release",
    "gui.update_none": "No newer release in this channel.",
    "gui.update_downgrade_confirm": "{version} is older than the running version. Older versions may not be able to read a database a newer one has already converted – a backup is made automatically before the update. Switch anyway?",
    "gui.update_now": "Update now",
    "gui.update_confirm": "Update Corsarr now? The bot restarts once.",
    "gui.updating_short": "updating",
    "gui.updating": "Update running – Corsarr restarts in a moment, and this page reconnects by itself.",
    "gui.update_docker": "Update on the Docker host with:",
    "gui.update_manual": "Update in the program directory with",
    "gui.update_unknown": "unknown",
    "gui.update_check_failed": "Could not reach GitHub: {error}",
    "gui.update_log": "Output of the last update",
    "gui.update_not_possible": "Updating from the web interface only works with the LXC/systemd installation.",
    "gui.setting_saved": "✓ saved",
    "gui.unsaved": "{n} unsaved change(s)",
    "gui.model_option_recommended": "{name} – recommended, about ${cost} per suggestion",
    "gui.model_option": "{name} – {factor}× the cost, about ${cost} per suggestion",
    "gui.model_warn": "{name} costs about {factor} times as much as Claude Haiku: around ${cost} per suggestion instead of $0.002. $5 of credit then lasts about {n} suggestions instead of around 2,500. For movie suggestions, Haiku is very likely all you need.",
    "gui.model_warn_strong": "⚠️ Very expensive!",
    "gui.model_confirm": "Really switch to {name}? It costs about {factor} times as much as Claude Haiku (about ${cost} per suggestion). Haiku is very likely all you need for movie suggestions.",
    "gui.user_not_found": "{name} (not found in Jellyfin)",
    "gui.options_fallback": "Could not load the list ({error}) – please enter it by hand.",
    "gui.options_need_key": "enter the API key first",
    "gui.provider_recommended": "{name} – recommended, tested",
    "gui.provider_untested": "{name} – untested",
    "gui.untested": "untested",
    "gui.provider_warn": "⚠️ {name} is not tested. Corsarr is only developed and tested with Claude. With other providers, suggestions may be worse, answers may fail or some features may not work. Claude (Haiku) is recommended: cheap and proven.",
    "gui.provider_local_hint": "Runs on your own machine, with no API costs. The address must be reachable from where Corsarr runs: in Docker or an LXC container, “localhost” is the container itself, not your computer. Small models often fail at the structured answers Corsarr needs.",
    "gui.provider_confirm": "{name} is not tested – suggestions may be worse or fail. Claude is recommended. Switch anyway?",
    "gui.cost_disclaimer": "About costs: Corsarr uses paid AI services with your own API key. You alone pay for them. No liability whatsoever is accepted for API costs, including unexpectedly high ones. Set a spending limit with your provider.",
    "gui.options_need_jellyfin": "enter the Jellyfin address and API key first",
    "gui.connections": "Connections",
    "gui.check_now": "Check now",
    "gui.checking": "Checking …",
    "gui.restart": "Restart bot",
    "gui.restarting": "Restarting …",
    "gui.group_telegram": "Telegram",
    "gui.group_llm": "AI provider",
    "gui.group_jellyfin": "Jellyfin",
    "gui.group_jellyseerr": "Jellyseerr / Seerr",
    "gui.group_webhooks": "Webhooks",
    "gui.group_interface": "Interface and updates",
    "gui.group_advanced": "Advanced",
    "gui.f_TELEGRAM_BOT_TOKEN": "Bot token",
    "gui.h_TELEGRAM_BOT_TOKEN": "From @BotFather.",
    "gui.f_TELEGRAM_CHAT_ID": "Group (chat id)",
    "gui.h_TELEGRAM_CHAT_ID": "A negative number – the setup assistant finds it for you.",
    "gui.f_NOTIFY_CHAT_ID": "Chat for download messages",
    "gui.h_NOTIFY_CHAT_ID": "Optional. Empty = the same group.",
    "gui.f_LLM_PROVIDER": "Provider",
    "gui.f_ANTHROPIC_API_KEY": "Claude API key",
    "gui.h_ANTHROPIC_API_KEY": "From the Claude Console (console.anthropic.com).",
    "gui.f_CLAUDE_MODEL": "Model",
    "gui.f_OPENAI_API_KEY": "OpenAI API key",
    "gui.h_OPENAI_API_KEY": "From platform.openai.com.",
    "gui.f_OPENAI_MODEL": "Model",
    "gui.f_GEMINI_API_KEY": "Gemini API key",
    "gui.h_GEMINI_API_KEY": "From Google AI Studio.",
    "gui.f_GEMINI_MODEL": "Model",
    "gui.f_OLLAMA_URL": "Ollama address",
    "gui.h_OLLAMA_URL": "e.g. http://192.168.1.30:11434",
    "gui.f_OLLAMA_MODEL": "Model",
    "gui.f_LMSTUDIO_URL": "LM Studio address",
    "gui.h_LMSTUDIO_URL": "e.g. http://192.168.1.30:1234",
    "gui.f_LMSTUDIO_MODEL": "Model",
    "gui.f_JELLYFIN_URL": "Address",
    "gui.h_JELLYFIN_URL": "e.g. http://192.168.1.20:8096",
    "gui.f_JELLYFIN_API_KEY": "API key",
    "gui.h_JELLYFIN_API_KEY": "Jellyfin dashboard → API Keys.",
    "gui.f_JELLYFIN_USER": "Shared account",
    "gui.h_JELLYFIN_USER": "The account you watch with together.",
    "gui.f_JELLYSEERR_URL": "Address",
    "gui.h_JELLYSEERR_URL": "e.g. http://192.168.1.21:5055",
    "gui.f_JELLYSEERR_API_KEY": "API key",
    "gui.h_JELLYSEERR_API_KEY": "In Jellyseerr under Settings → General.",
    "gui.f_WEBHOOK_SECRET": "Webhook secret",
    "gui.h_WEBHOOK_SECRET": "Shared secret for Jellyfin, Sonarr and Radarr. The ready-made addresses are under Setup → Webhooks.",
    "gui.f_ADMIN_PASSWORD": "Password for this interface",
    "gui.h_ADMIN_PASSWORD": "Optional. Without one there is no login – anyone on your home network can change settings.",
    "gui.f_LANGUAGE": "Language",
    "gui.h_LANGUAGE": "Of the interface. The bot answers in the language it is written to in.",
    "gui.f_UPDATE_CHANNEL": "Update channel",
    "gui.h_UPDATE_CHANNEL": "Stable = tested releases, Beta = pre-releases, Development = every new commit.",
    "gui.f_WEBHOOK_HOST": "Listens on",
    "gui.h_WEBHOOK_HOST": "0.0.0.0 = all network interfaces. Takes effect after restarting the program.",
    "gui.f_WEBHOOK_PORT": "Port",
    "gui.h_WEBHOOK_PORT": "For the interface and webhooks. Takes effect after restarting the program.",
    "gui.f_LOG_LEVEL": "Log level",
    "gui.f_DATA_DIR": "Data directory",
    "gui.h_DATA_DIR": "Can only be changed with the DATA_DIR environment variable.",
    "gui.group_missing_one": "1 field missing",
    "gui.group_missing": "{n} fields missing",
    "gui.field_missing": "Required – please fill in.",
    "gui.group_changed": "changed",
    "gui.group_ok": "✓",
    "gui.password_set": "password set",
    "gui.password_none": "no password",
    "gui.secret_is_set": "set",
    "gui.env_tag": "ENV",
    "gui.from_env_title": "Comes from an environment variable or the .env file. A value entered here takes precedence.",
    "gui.reset_short": "↺ default",
    "gui.deck_ok": "connected",
    "gui.deck_error": "error",
    "gui.deck_wait": "waiting",
    "gui.f_SONARR_URL": "Sonarr address",
    "gui.h_SONARR_URL": "Optional – only for the status check.",
    "gui.f_SONARR_API_KEY": "Sonarr API key",
    "gui.h_SONARR_API_KEY": "In Sonarr under Settings → General.",
    "gui.f_RADARR_URL": "Radarr address",
    "gui.h_RADARR_URL": "Optional – only for the status check.",
    "gui.f_RADARR_API_KEY": "Radarr API key",
    "gui.h_RADARR_API_KEY": "In Radarr under Settings → General.",
    "gui.cred_SONARR_API_KEY": "Sonarr API key",
    "gui.cred_RADARR_API_KEY": "Radarr API key",
    "gui.wiz_arr_remember": "Remember access – the status page then checks {name} and the webhook actively",
    "gui.svc_telegram": "Telegram",
    "gui.svc_llm": "AI provider",
    "gui.svc_jellyfin": "Jellyfin",
    "gui.svc_jellyseerr": "Jellyseerr",
    "gui.svc_webhook": "Jellyfin webhook",
    "gui.svc_sonarr": "Sonarr",
    "gui.svc_radarr": "Radarr",
    "gui.status_ok": "OK",
    "gui.status_error": "Error",
    "gui.status_unknown": "unknown",
    "gui.status_disabled": "not configured",
    "gui.last_check": "checked",
    "gui.last_ok": "last OK",
    "gui.never": "never",
    "gui.ago_s": "{n} s ago",
    "gui.ago_m": "{n} min ago",
    "gui.ago_h": "{n} h ago",
    "gui.ago_d": "{n} days ago",
    "gui.uptime": "running since",
    "gui.data_dir": "Data directory",
    "gui.log_file": "Log file",
    "gui.level_all": "All",
    "gui.level_info": "Info and above",
    "gui.level_warning": "Warnings and errors",
    "gui.level_error": "Errors only",
    "gui.search": "Search …",
    "gui.autoscroll": "Follow",
    "gui.no_events": "No events",
    "gui.events_hint": "Shows the last 1000 entries since the program started. Older ones are in the log file.",
    "gui.save": "Save",
    "gui.saving": "Saving …",
    "gui.saved": "Saved",
    "gui.saved_restart": "Saved. The bot restarts with the new values.",
    "gui.saved_app_restart": "Saved. Web server address and port take effect after restarting the whole "
                             "program.",
    "gui.save_failed": "Saving failed",
    "gui.secret_set": "set (leave empty to keep)",
    "gui.secret_unset": "not set",
    "gui.from_env": "from environment",
    "gui.from_gui": "changed in the GUI",
    "gui.reset": "reset",
    "gui.reset_hint": "Discard the GUI value and use the value from the environment or .env again",
    "gui.readonly": "can only be changed via environment variable",
    "gui.behaviour": "Bot behaviour",
    "gui.behaviour_hint": "Applies immediately. Can also be changed in the chat, e.g. “@bot no more pirate”.",
    "gui.connection_settings": "Connections and system",
    "gui.connection_hint": "Changes take effect with “Save”; the bot then restarts.",
    "gui.required": "required",
    "gui.network_error": "No connection to the bot – is the program still running?",
    "gui.retry": "Try again",
    "gui.skin": "Design",
    "gui.skin_arr": "*arr",
    "gui.skin_terminal": "Terminal",
    "gui.skin_vhs": "Video store",
    "gui.skin_soft": "Friendly",
    "gui.col_service": "Service",
    "gui.col_status": "Status",
    "gui.col_detail": "Details",
    "gui.col_checked": "Checked",
    "gui.services_ok": "{n}/{total} ok",
    "gui.s_series_pause_days": "Series pause: ask after … days",
    "gui.s_abort_days": "Abandoned movies: ask after … days",
    "gui.characters": "Characters",
    "gui.characters_hint": "Each message is spoken by one of the active characters, now and then two talk to each other. All off = a plain tone. In the chat e.g. “@bot switch grandma on”.",
    "gui.s_pirate_enabled": "🏴‍☠️ Pirate",
    "gui.s_genz_enabled": "📱 Gen Z",
    "gui.s_butler_enabled": "🎩 Butler",
    "gui.s_critic_enabled": "🧐 Film critic",
    "gui.s_clerk_enabled": "📼 Video store clerk",
    "gui.s_noir_enabled": "🕵️ Film noir detective",
    "gui.s_trailer_enabled": "🎙️ Trailer voice",
    "gui.s_computer_enabled": "🤖 Ship's computer",
    "gui.s_grandma_enabled": "👵 Grandma",
    "gui.s_reporter_enabled": "⚽ Sports commentator",
    "gui.s_cat_enabled": "🐈 Cat",
    "gui.s_bard_enabled": "🧙 Bard",
}

TEXTS = {"de": DE, "en": EN}
