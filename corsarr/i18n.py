"""User-facing texts in German and English: Telegram, prompts, check and GUI; log lines in English.

`t("key", name=value)` returns the text in the active language. Keys are grouped by prefix:
bot.* fixed Telegram texts and buttons, sit.* situations the model phrases, prompt.* model
instructions, log.* log lines, check.* connectivity check, cfg.* config errors, gui.* web interface.

Two levels of language:
- the default (setting LANGUAGE): web interface, and the bot until the group has written to it;
- per request: the bot answers in the language it was addressed in. `use_language()` sets it for the
  current task only, so a German and an English message handled at the same time don't mix.
"""
from __future__ import annotations

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


def supported(lang: str | None) -> str:
    """Map any language code to one with texts: German stays German, everything else gets English."""
    return "de" if (lang or "").lower().startswith("de") else "en"


@contextmanager
def use_language(lang: str | None) -> Iterator[None]:
    token = _current.set(supported(lang) if lang else None)
    try:
        yield
    finally:
        _current.reset(token)


def switch_language(lang: str) -> None:
    """Change the language inside a `use_language` block, e.g. once a message's language is known."""
    _current.set(supported(lang))


def t(key: str, **kwargs) -> str:
    lang = "en" if key.startswith("log.") else language()  # logs are technical: always English
    text = TEXTS[lang].get(key) or TEXTS["en"].get(key) or key
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
nach dem Schauen nach Feedback und merkst dir ihren Geschmack. Du schreibst auf Deutsch.

Der Code liefert dir alle Fakten (Titel, Inhalte, Laufzeiten, Bewertungen, Verfügbarkeit). Erfinde \
niemals Titel, Inhalte, Besetzung, Jahreszahlen oder Bewertungen; nutze nur, was dir gegeben wird. \
Halte dich kurz: Telegram-Nachrichten, keine Romane, kein Markdown außer Emojis.

{characters}""",
    "prompt.characters": """Die Figuren:
- {pirate} Pirat: alter Seebär mit trockenem Humor. Seemannsbilder ja, aber sparsam und abwechslungsreich – \
nicht in jedem Satz, und kein "Arrr" in jeder Nachricht. Übertreibt gern ein bisschen, bleibt hilfsbereit.
- {genz} Gen Z: Mitte zwanzig, trocken und selbstironisch. Klingt wie eine echte Person im Gruppenchat, \
nicht wie eine Slang-Parodie: höchstens ein Slangwort oder Anglizismus pro Nachricht, oft gar keins. \
Abgenutzte Floskeln ("no cap", "slay", "lowkey", "fr", "real", "bestie") meiden. Der Witz kommt aus \
konkreten, leicht sarkastischen Beobachtungen zum Film. Kurze Sätze, gern kleingeschrieben.

Für beide: Beziehe dich auf den konkreten Titel – Inhalt, Stimmung, Regie, eine Besonderheit – statt auf \
Allgemeinplätze, die zu jedem Film passen würden.

Regeln für die Figuren:
- Jede Zeile gehört genau einer Figur und beginnt mit deren Emoji ({pirate} oder {genz}) und einem Leerzeichen.
- Innerhalb einer Zeile wird nie zwischen den Figuren gewechselt; ein Figurwechsel bedeutet immer eine neue Zeile.
- Im Dialog reden beide kurz miteinander oder ergänzen sich, höchstens 4 Zeilen insgesamt.
- Im normalen Modus gibt es keine Figuren und keine Figuren-Emojis: freundlich, knapp, sachlich.""",
    "prompt.speaker_pirate": "Sprecher dieser Nachricht: nur der Pirat. Jede Zeile beginnt mit {pirate}.",
    "prompt.speaker_genz": "Sprecher dieser Nachricht: nur Gen Z. Jede Zeile beginnt mit {genz}.",
    "prompt.speaker_dialog": "Sprecher dieser Nachricht: beide im kurzen Dialog, abwechselnd zeilenweise "
                             "({pirate} und {genz}).",
    "prompt.speaker_normal": "Sprecher dieser Nachricht: normaler Modus, keine Figuren, keine Figuren-Emojis.",
    "prompt.genres_jellyfin": "Jellyfin-Genreliste",
    "prompt.genres_tmdb_movie": "TMDB-Filmgenres",
    "prompt.genres_tmdb_tv": "TMDB-Seriengenres",
    "prompt.understand": """Aufgabe: Ordne die Nachricht ein und gib die Felder aus.

language: die Sprache, in der die Nachricht geschrieben ist, als ISO-Code (de, en, …).

intent:
- recommend: sie wollen Vorschläge (Bibliothek zuerst), auch "passend zu dem, was wir zuletzt geschaut haben".
- new_only: sie wollen ausdrücklich Neues, das noch nicht in der Bibliothek ist ("such nach Neuem").
- settings: sie ändern eine Einstellung (Serien-Pause in Tagen, Abbruch-Nachfrage in Tagen, Pirat an/aus, \
Gen Z an/aus). Wochen in Tage umrechnen. Nur geänderte Felder setzen, sonst null.
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
    "check.jellyfin": "Benutzer-ID {uid}, {n} Genres (z. B. {examples})",
    "check.jellyseerr": "{n} TMDB-Filmgenres (z. B. {examples})",
    "check.llm_ping": "{provider}: {model} antwortet",
    "check.llm_model": "{provider}: Modell {model} verfügbar",
    "check.telegram": "@{username}, Gruppe „{chat}“, Privacy-Modus {privacy}",
    "check.privacy_off": "aus ✔",
    "check.privacy_on": "AN – bei BotFather /setprivacy → Disable",
    "check.not_configured": "nicht konfiguriert",
    "check.webhook_never": "Noch kein Ereignis von Jellyfin empfangen",
    "check.arr_never": "Noch kein Ereignis empfangen – in Sonarr/Radarr unter Connect einrichten und „Test“ drücken",
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
    "gui.secret_set": "gesetzt – leer lassen, um nichts zu ändern",
    "gui.secret_unset": "nicht gesetzt",
    "gui.from_env": "aus Umgebungsvariable",
    "gui.from_gui": "in der GUI geändert",
    "gui.reset": "zurücksetzen",
    "gui.reset_hint": "GUI-Wert verwerfen und wieder den Wert aus der Umgebung bzw. .env nutzen",
    "gui.readonly": "nur über Umgebungsvariable änderbar",
    "gui.behaviour": "Bot-Verhalten",
    "gui.behaviour_hint": "Gilt sofort. Lässt sich auch im Chat ändern, z. B. „@bot kein Pirat mehr“.",
    "gui.connection_settings": "Verbindungen und System",
    "gui.connection_hint": "Änderungen starten den Bot neu. Gespeichert wird in config.json im "
                           "Datenverzeichnis; diese Werte gehen vor Umgebungsvariablen und .env.",
    "gui.required": "Pflichtfeld",
    "gui.network_error": "Keine Verbindung zum Bot – läuft das Programm noch?",
    "gui.retry": "Erneut versuchen",
    "gui.group_telegram": "Telegram",
    "gui.group_llm": "KI-Anbieter (Sprachmodell)",
    "gui.group_jellyfin": "Jellyfin",
    "gui.group_jellyseerr": "Jellyseerr",
    "gui.group_web": "Webserver und Webhook",
    "gui.group_system": "System",
    "gui.f_TELEGRAM_BOT_TOKEN": "Bot-Token von @BotFather",
    "gui.f_TELEGRAM_CHAT_ID": "Chat-ID der Gruppe (negativ)",
    "gui.f_NOTIFY_CHAT_ID": "Chat-ID für Download-Meldungen von Sonarr/Radarr (optional, leer = Gruppe oben)",
    "gui.f_LLM_PROVIDER": "Anbieter",
    "gui.f_ANTHROPIC_API_KEY": "Claude-API-Schlüssel",
    "gui.f_CLAUDE_MODEL": "Claude-Modell",
    "gui.f_OPENAI_API_KEY": "OpenAI-API-Schlüssel",
    "gui.f_OPENAI_MODEL": "OpenAI-Modell",
    "gui.f_GEMINI_API_KEY": "Gemini-API-Schlüssel (Google AI Studio)",
    "gui.f_GEMINI_MODEL": "Gemini-Modell",
    "gui.f_OLLAMA_URL": "Ollama-Adresse, z. B. http://192.168.1.30:11434",
    "gui.f_OLLAMA_MODEL": "Ollama-Modell",
    "gui.f_LMSTUDIO_URL": "LM-Studio-Adresse, z. B. http://192.168.1.30:1234",
    "gui.f_LMSTUDIO_MODEL": "LM-Studio-Modell",
    "gui.f_JELLYFIN_URL": "Adresse, z. B. http://192.168.1.20:8096",
    "gui.f_JELLYFIN_API_KEY": "API-Schlüssel",
    "gui.f_JELLYFIN_USER": "Gemeinsames Konto (Name oder ID)",
    "gui.f_JELLYSEERR_URL": "Adresse, z. B. http://192.168.1.21:5055",
    "gui.f_JELLYSEERR_API_KEY": "API-Schlüssel",
    "gui.f_WEBHOOK_HOST": "Adresse, auf der der Webserver lauscht",
    "gui.f_WEBHOOK_PORT": "Port",
    "gui.f_WEBHOOK_SECRET": "Webhook-Secret – in Jellyfin als Header X-Corsarr-Secret eintragen",
    "gui.f_ADMIN_PASSWORD": "Passwort für diese Oberfläche (optional – ohne Passwort kein Login)",
    "gui.f_LANGUAGE": "Sprache der Oberfläche – und des Bots, bis ihn jemand anschreibt (dann antwortet er in dessen Sprache)",
    "gui.f_LOG_LEVEL": "Log-Stufe",
    "gui.f_DATA_DIR": "Datenverzeichnis",
    "gui.s_series_pause_days": "Serien-Pause: nachfragen nach … Tagen",
    "gui.s_abort_days": "Abbruch-Nachfrage nach … Tagen",
    "gui.s_pirate_enabled": "🏴‍☠️ Pirat aktiv",
    "gui.s_genz_enabled": "📱 Gen Z aktiv",
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
watching and remember their taste. You write in English.

The code gives you all facts (titles, plots, runtimes, ratings, availability). Never invent titles, \
plots, cast, years or ratings; only use what you are given. Keep it short: Telegram messages, no \
essays, no Markdown except emojis.

{characters}""",
    "prompt.characters": """The characters:
- {pirate} Pirate: an old sea dog with a dry sense of humour. Nautical imagery yes, but sparingly and \
varied – not in every sentence, and no "Arrr" in every message. Exaggerates a little, stays helpful.
- {genz} Gen Z: mid-twenties, dry and self-deprecating. Sounds like a real person in a group chat, not a \
slang parody: at most one slang word per message, often none. Avoid worn-out phrases ("no cap", "slay", \
"lowkey", "fr", "real", "bestie"). The humour comes from concrete, slightly sarcastic observations about \
the film. Short sentences, often lowercase.

For both: refer to the concrete title – plot, mood, director, something special – instead of \
generic lines that would fit any film.

Rules for the characters:
- Every line belongs to exactly one character and starts with its emoji ({pirate} or {genz}) and a space.
- Never switch characters within a line; a change of character always means a new line.
- In a dialog both talk briefly with each other or add to each other, at most 4 lines in total.
- In normal mode there are no characters and no character emojis: friendly, brief, factual.""",
    "prompt.speaker_pirate": "Speaker of this message: only the pirate. Every line starts with {pirate}.",
    "prompt.speaker_genz": "Speaker of this message: only Gen Z. Every line starts with {genz}.",
    "prompt.speaker_dialog": "Speaker of this message: both in a short dialog, alternating line by line "
                             "({pirate} and {genz}).",
    "prompt.speaker_normal": "Speaker of this message: normal mode, no characters, no character emojis.",
    "prompt.genres_jellyfin": "Jellyfin genre list",
    "prompt.genres_tmdb_movie": "TMDB movie genres",
    "prompt.genres_tmdb_tv": "TMDB TV genres",
    "prompt.understand": """Task: classify the message and fill in the fields.

language: the language the message is written in, as ISO code (de, en, …).

intent:
- recommend: they want suggestions (library first), also "something that fits what we watched lately".
- new_only: they explicitly want something new that is not in the library yet ("look for something new").
- settings: they change a setting (series pause in days, abort follow-up in days, pirate on/off, \
Gen Z on/off). Convert weeks to days. Only set changed fields, otherwise null.
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
    "check.jellyfin": "user id {uid}, {n} genres (e.g. {examples})",
    "check.jellyseerr": "{n} TMDB movie genres (e.g. {examples})",
    "check.llm_ping": "{provider}: {model} responds",
    "check.llm_model": "{provider}: model {model} available",
    "check.telegram": "@{username}, group “{chat}”, privacy mode {privacy}",
    "check.privacy_off": "off ✔",
    "check.privacy_on": "ON – at BotFather /setprivacy → Disable",
    "check.not_configured": "not configured",
    "check.webhook_never": "No event received from Jellyfin yet",
    "check.arr_never": "No event received yet – set it up in Sonarr/Radarr under Connect and press “Test”",
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
    "gui.secret_set": "set – leave empty to keep it",
    "gui.secret_unset": "not set",
    "gui.from_env": "from environment",
    "gui.from_gui": "changed in the GUI",
    "gui.reset": "reset",
    "gui.reset_hint": "Discard the GUI value and use the value from the environment or .env again",
    "gui.readonly": "can only be changed via environment variable",
    "gui.behaviour": "Bot behaviour",
    "gui.behaviour_hint": "Applies immediately. Can also be changed in the chat, e.g. “@bot no more pirate”.",
    "gui.connection_settings": "Connections and system",
    "gui.connection_hint": "Changes restart the bot. Values are stored in config.json in the data directory "
                           "and take precedence over environment variables and .env.",
    "gui.required": "required",
    "gui.network_error": "No connection to the bot – is the program still running?",
    "gui.retry": "Try again",
    "gui.group_telegram": "Telegram",
    "gui.group_llm": "AI provider (language model)",
    "gui.group_jellyfin": "Jellyfin",
    "gui.group_jellyseerr": "Jellyseerr",
    "gui.group_web": "Web server and webhook",
    "gui.group_system": "System",
    "gui.f_TELEGRAM_BOT_TOKEN": "Bot token from @BotFather",
    "gui.f_TELEGRAM_CHAT_ID": "Group chat id (negative)",
    "gui.f_NOTIFY_CHAT_ID": "Chat id for Sonarr/Radarr download messages (optional, empty = group above)",
    "gui.f_LLM_PROVIDER": "Provider",
    "gui.f_ANTHROPIC_API_KEY": "Claude API key",
    "gui.f_CLAUDE_MODEL": "Claude model",
    "gui.f_OPENAI_API_KEY": "OpenAI API key",
    "gui.f_OPENAI_MODEL": "OpenAI model",
    "gui.f_GEMINI_API_KEY": "Gemini API key (Google AI Studio)",
    "gui.f_GEMINI_MODEL": "Gemini model",
    "gui.f_OLLAMA_URL": "Ollama address, e.g. http://192.168.1.30:11434",
    "gui.f_OLLAMA_MODEL": "Ollama model",
    "gui.f_LMSTUDIO_URL": "LM Studio address, e.g. http://192.168.1.30:1234",
    "gui.f_LMSTUDIO_MODEL": "LM Studio model",
    "gui.f_JELLYFIN_URL": "Address, e.g. http://192.168.1.20:8096",
    "gui.f_JELLYFIN_API_KEY": "API key",
    "gui.f_JELLYFIN_USER": "Shared account (name or id)",
    "gui.f_JELLYSEERR_URL": "Address, e.g. http://192.168.1.21:5055",
    "gui.f_JELLYSEERR_API_KEY": "API key",
    "gui.f_WEBHOOK_HOST": "Address the web server listens on",
    "gui.f_WEBHOOK_PORT": "Port",
    "gui.f_WEBHOOK_SECRET": "Webhook secret – enter in Jellyfin as header X-Corsarr-Secret",
    "gui.f_ADMIN_PASSWORD": "Password for this interface (optional – without one there is no login)",
    "gui.f_LANGUAGE": "Language of the interface – and of the bot until someone writes to it (it then answers in that language)",
    "gui.f_LOG_LEVEL": "Log level",
    "gui.f_DATA_DIR": "Data directory",
    "gui.s_series_pause_days": "Series pause: ask after … days",
    "gui.s_abort_days": "Abandoned movies: ask after … days",
    "gui.s_pirate_enabled": "🏴‍☠️ Pirate active",
    "gui.s_genz_enabled": "📱 Gen Z active",
}

TEXTS = {"de": DE, "en": EN}
