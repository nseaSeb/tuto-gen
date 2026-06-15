"""Lecture (Play) de l'aperçu + synthèse vocale / cache audio."""

from __future__ import annotations

import queue
import subprocess
import tempfile
import threading
import time
from contextlib import redirect_stdout
from pathlib import Path

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageTk

from .. import composer, config, paquet, settings
from ..cli import _ouvrir, _slug, construire
from .common import (
    VERT, TL_LEFT, TL_TOP, TL_ROW_H, TL_GAP, TL_HANDLE, TL_MIN_BODY,
    SETTINGS_W, LIST_W, COUL, PRESETS_TEXTE, ROLE_LABEL,
    _to_float, _lighten, _parse_fin, _Q,
)


class PlaybackMixin:
    # ===================================================== LECTURE (PLAY)
    def _play_toggle(self, mode="tuto"):
        if self._playing or self._play_intent:
            self._play_stop()
            return
        if not self.scenes:
            return
        if mode == "slide":
            if self.current is None:
                return
            scenes = [self.scenes[self.current]]
            indices = [self.current]
            msg = "\n▶ Lecture de la slide — préparation de l'audio…\n"
        else:
            scenes = list(self.scenes)
            indices = list(range(len(scenes)))
            msg = "\n▶ Lecture du tuto — préparation de l'audio…\n"
        self._play_mode = mode
        self._play_intent = True
        self._maj_boutons_play("prepare")
        self._log(msg)
        threading.Thread(target=self._play_prepare,
                         args=(scenes, indices, self.meta), daemon=True).start()

    def _maj_boutons_play(self, etat: str):
        """Met à jour les deux boutons de lecture selon l'état.

        `etat` ∈ {"idle", "prepare", "play"} : seul le bouton du mode actif
        se transforme (Préparation/Stop) ; l'autre est désactivé pendant la
        lecture pour éviter deux lectures simultanées."""
        paires = [("tuto", getattr(self, "btn_play", None), "▶  Lire le tuto"),
                  ("slide", getattr(self, "btn_play_slide", None),
                   "▶  Lire la slide")]
        for mode, btn, label in paires:
            if btn is None:
                continue
            try:
                if etat == "idle":
                    btn.config(text=label, state="normal")
                elif mode == self._play_mode:
                    btn.config(state="normal",
                               text="⏳  Préparation…" if etat == "prepare"
                               else "⏹  Stop")
                else:
                    btn.config(state="disabled")
            except Exception:
                pass

    def _play_prepare(self, scenes, indices, meta):
        """Synthèse des narrations + planning (scènes + événements audio).

        `indices[i]` est l'indice réel (dans `self.scenes`) de `scenes[i]`,
        afin que la lecture surligne la bonne scène (utile en mode « slide »)."""
        from .. import assembler, tts
        self._avertir_telechargement_modele()
        try:
            tmp = Path(tempfile.mkdtemp(prefix="tutoplay_"))
            plan_scenes, audio = [], []
            start = 0.0
            for idx, scene in enumerate(scenes):
                narrs = []
                for j, n in enumerate(scene.narrations):
                    txt = (n.texte or "").strip()
                    if not txt:
                        continue
                    p = config.params_voix(meta, n)
                    cle = (txt, str(p["ref_voix"]), p["speaker"], p["speed"],
                           p["temperature"], p["fluidite"])
                    clip = self._play_cache.get(cle)
                    if clip is None:
                        try:
                            clip = tts.synthetiser(txt, meta.voix,
                                                   tmp / f"{idx}_{j}.wav", **p)
                        except Exception as e:
                            self.q.put(f"   ⚠ narration {idx+1}.{j+1} muette : {e}\n")
                            clip = None
                        if clip is not None:
                            self._play_cache[cle] = clip
                    if clip is not None:
                        narrs.append(assembler.NarrationRendue(clip=clip,
                                                               debut=n.debut))
                        audio.append((start + n.debut, str(clip.chemin), 1.0))
                duree = assembler.duree_scene(
                    assembler.SceneRendue(scene=scene, narrations=narrs))
                for sa in scene.samples:
                    if sa.chemin and Path(sa.chemin).is_file():
                        audio.append((start + sa.debut, str(sa.chemin),
                                      max(0.0, sa.volume)))
                plan_scenes.append({"idx": indices[idx], "start": start,
                                    "duree": duree})
                start += duree
            planning = {"scenes": plan_scenes, "audio": sorted(audio),
                        "total": start}
            self.q.put(("__PLAY_READY__", planning))
        except Exception as e:
            self.q.put(f"   ✗ Lecture impossible : {e}\n")
            self.q.put(("__PLAY_READY__", None))

    def _play_start(self, planning):
        if planning is None or not planning["scenes"]:
            self._play_stop()
            return
        self._play_schedule = planning
        for ev in planning["audio"]:
            # marqueur "non lancé"
            pass
        self._play_lances = set()
        self._playing = True
        self._play_intent = False
        self._maj_boutons_play("play")
        self._play_t0 = time.monotonic()
        self._play_tick()

    @staticmethod
    def _play_scene_at(plan, elapsed):
        """Renvoie (idx_scène, t_local) pour un temps écoulé global."""
        for sc in plan["scenes"]:
            if sc["start"] <= elapsed < sc["start"] + sc["duree"]:
                return sc["idx"], elapsed - sc["start"]
        sc = plan["scenes"][-1]
        return sc["idx"], sc["duree"]

    def _play_tick(self):
        if not self._playing or not self._play_schedule:
            return
        elapsed = time.monotonic() - self._play_t0
        plan = self._play_schedule
        if elapsed >= plan["total"]:
            self._play_stop()
            return

        scene_idx, t_local = self._play_scene_at(plan, elapsed)
        change_scene = scene_idx != self.current
        if change_scene:
            self.current = scene_idx
            self._maj_surbrillance()
        self._preview_t = round(t_local, 2)
        self._draw_apercu()
        # Même scène → on déplace juste le curseur ; nouvelle scène → redraw
        # complet (les pistes changent).
        if change_scene:
            self._draw_timeline()
        else:
            self._maj_curseur_tl()

        # Événements audio échus, lancés une seule fois
        for i, (t_abs, chemin, vol) in enumerate(plan["audio"]):
            if i in self._play_lances:
                continue
            if t_abs <= elapsed:
                self._play_lances.add(i)
                try:
                    p = subprocess.Popen(["afplay", "-v", f"{vol:.2f}", chemin])
                    self._play_audio.append(p)
                except Exception:
                    pass

        self._play_job = self.root.after(80, self._play_tick)

    def _play_stop(self):
        self._playing = False
        self._play_intent = False
        if self._play_job is not None:
            try:
                self.root.after_cancel(self._play_job)
            except Exception:
                pass
            self._play_job = None
        for p in self._play_audio:
            try:
                p.terminate()
            except Exception:
                pass
        self._play_audio = []
        self._maj_boutons_play("idle")

    # ================================================================= TTS
    def _ecouter(self, narration):
        phrase = (getattr(narration, "texte", "") or "").strip()
        if not phrase:
            self._log("   (pas de narration)\n")
            return
        if self.btn_ecoute and self.btn_ecoute.winfo_exists():
            self.btn_ecoute.config(state="disabled")
        self._log("\n🔊 Écoute…\n")
        p = config.params_voix(self.meta, narration)
        threading.Thread(
            target=self._worker_tts,
            args=(phrase, self.meta.voix, p),
            daemon=True).start()

    def _avertir_telechargement_modele(self):
        """Au 1er usage, prévient si le moteur vocal doit être téléchargé.

        Le modèle XTTS n'est plus embarqué (build allégé) : il est récupéré
        automatiquement la première fois (~1,8 Go), puis disponible hors-ligne.
        Message affiché une seule fois par session pour éviter le spam."""
        if getattr(self, "_modele_avise", False):
            return
        try:
            from .. import tts
            if not tts.modele_local_disponible():
                self._modele_avise = True
                self.q.put("\n⏳ Premier lancement : téléchargement du moteur "
                           "vocal (~1,8 Go, une seule fois). Cela peut prendre "
                           "quelques minutes selon la connexion…\n")
        except Exception:
            pass

    def _worker_tts(self, phrase: str, voix: str, params: dict):
        from .. import tts
        self._avertir_telechargement_modele()
        try:
            tmp = Path(tempfile.mkdtemp(prefix="tutoecoute_"))
            clip = tts.synthetiser(phrase, voix, tmp / "ecoute.wav", **params)
            if clip:
                self.q.put(f"   ✓ {clip.duree:.1f}s ({clip.backend}) — lecture…\n")
                # L'audio est maintenant en cache : recale les pistes narration
                # de la timeline sur leur durée audio réelle.
                self._rafraichir_narr_dur()
                subprocess.run(["afplay", str(clip.chemin)], check=False)
            else:
                self.q.put("   (rien à synthétiser)\n")
        except Exception as e:
            self._signaler_echec_tts(None, "Écoute de la narration", e,
                                     notifier=True)
        self.q.put(("__ECOUTE__",))

    def _rafraichir_narr_dur(self):
        """Vide le cache des durées de narration et redessine la timeline
        (thread-safe). Appelé quand un audio vient d'être (re)généré."""
        def _do():
            self._narr_durees.clear()
            # L'audio vient d'apparaître : on recale la narration sélectionnée
            # sur sa durée audio (début borné, slide allongée si besoin).
            if (self._sel and self._sel.get("kind") == "narration"
                    and self.current is not None):
                try:
                    n = self.scenes[self.current].narrations[self._sel["idx"]]
                    self._normaliser_narration(n)
                except (IndexError, KeyError, TypeError):
                    pass
            self._draw_timeline()
            maj = getattr(self, "_narr_dur_maj", None)
            if maj:
                try:
                    maj()
                except Exception:
                    pass
        try:
            self.root.after(0, _do)
        except Exception:
            pass

    def _set_cache_status(self, lbl, texte: str, couleur: str):
        """Met à jour l'indicateur de cache (thread-safe via root.after).

        Réinitialise aussi un éventuel état « échec cliquable » posé par
        `_signaler_echec_tts` (curseur main + clic), afin qu'un statut normal
        (« ✓ audio prêt », « ⏳ génération… ») ne reste pas cliquable."""
        if lbl is None:
            return
        def _do():
            try:
                if lbl.winfo_exists():
                    lbl.config(text=texte, fg=couleur, cursor="")
                    lbl.unbind("<Button-1>")
            except Exception:
                pass
        try:
            self.root.after(0, _do)
        except Exception:
            pass

    def _signaler_echec_tts(self, lbl, contexte: str, erreur,
                            notifier: bool = False):
        """Rend lisible l'échec d'une synthèse vocale (cf. demande : expliquer
        *pourquoi* une narration échoue).

        Le journal n'étant visible qu'en mode « Génération », on :
          1. journalise la raison exacte ;
          2. transforme l'indicateur de cache en bouton cliquable rouvrant la
             raison complète dans une boîte de dialogue (consultable à tout
             moment, même journal masqué) ;
          3. affiche en plus une notification immédiate si `notifier` (pour les
             actions explicites comme « Écouter », où l'utilisateur attend un
             retour direct).
        """
        raison = str(erreur).strip() or erreur.__class__.__name__
        self.q.put(f"   ✗ {contexte} : {raison}\n")

        def _do():
            if lbl is not None:
                try:
                    if lbl.winfo_exists():
                        lbl.config(text="✗ échec — cliquer pour la raison",
                                   fg="#c06060", cursor="hand2")
                        lbl.unbind("<Button-1>")
                        lbl.bind("<Button-1>", lambda _e: messagebox.showerror(
                            "Échec de la génération de la narration",
                            f"{contexte} :\n\n{raison}"))
                except Exception:
                    pass
            if notifier:
                messagebox.showerror(
                    "Échec de la génération de la narration",
                    f"{contexte} :\n\n{raison}")
        try:
            self.root.after(0, _do)
        except Exception:
            pass

    def _maj_cache_status(self, narration, lbl):
        """Affiche l'état courant du cache pour cette narration."""
        from .. import tts
        texte = (getattr(narration, "texte", "") or "").strip()
        if not texte:
            self._set_cache_status(lbl, "", "#888")
            return
        if tts.en_cache(texte, **config.params_voix(self.meta, narration)):
            self._set_cache_status(lbl, "✓ audio prêt (cache)", "#46a06a")
        else:
            self._set_cache_status(lbl, "○ audio non généré", "#888")

    def _prechauffer_narration(self, narration, status_lbl=None):
        """Pré-génère l'audio d'une narration en cache (thread de fond), pour
        que la génération de la vidéo réutilise un audio déjà prêt."""
        from .. import tts
        texte = (getattr(narration, "texte", "") or "").strip()
        if not texte:
            self._set_cache_status(status_lbl, "", "#888")
            return
        p = config.params_voix(self.meta, narration)
        if tts.en_cache(texte, **p):
            self._set_cache_status(status_lbl, "✓ audio prêt (cache)", "#46a06a")
            return
        self._set_cache_status(status_lbl, "⏳ génération de l'audio…", "#caa44a")
        self._log("⏳ Pré-génération de l'audio en cache…\n")
        threading.Thread(
            target=self._worker_prechauffe,
            args=(texte, p, status_lbl),
            daemon=True).start()

    def _worker_prechauffe(self, texte, params, status_lbl=None):
        from .. import tts
        import time
        self._avertir_telechargement_modele()
        t0 = time.time()
        try:
            tts.prechauffer(texte, **params)
            self.q.put(f"   ✓ audio en cache ({time.time() - t0:.1f}s)\n")
            self._set_cache_status(status_lbl, "✓ audio prêt (cache)", "#46a06a")
            # L'audio est désormais en cache : recalcule la durée des pistes
            # narration et redessine la timeline à leur longueur audio réelle.
            self._rafraichir_narr_dur()
        except Exception as e:
            self._signaler_echec_tts(status_lbl, "Mise en cache de l'audio", e)

    def _regenerer_narration(self, narration, status_lbl=None):
        """Force un nouveau take (XTTS étant stochastique) en invalidant le
        cache de cette narration, puis resynthétise en arrière-plan."""
        from .. import tts
        texte = (getattr(narration, "texte", "") or "").strip()
        if not texte:
            return
        p = config.params_voix(self.meta, narration)
        self._set_cache_status(status_lbl, "⏳ nouvelle prise…", "#caa44a")
        self._log("🔄 Régénération de l'audio (nouvelle prise)…\n")

        def work():
            self._avertir_telechargement_modele()
            try:
                duree = tts.regenerer(texte, **p)
                if duree is not None:
                    self.q.put(f"   ✓ nouvelle prise ({duree:.1f}s)\n")
                    self._set_cache_status(status_lbl, "✓ audio prêt (cache)",
                                           "#46a06a")
                    self._rafraichir_narr_dur()
                else:
                    self.q.put("   (rien à régénérer)\n")
            except Exception as e:
                self._signaler_echec_tts(status_lbl, "Régénération de l'audio", e)
        threading.Thread(target=work, daemon=True).start()

    def _play_sample(self, chemin):
        if not chemin or not Path(chemin).is_file():
            self._log("   ⚠ Fichier introuvable\n")
            return
        def _run():
            try:
                subprocess.run(["afplay", str(chemin)], check=False)
            except Exception as e:
                self.q.put(f"   ✗ lecture impossible : {e}\n")
        threading.Thread(target=_run, daemon=True).start()
        self._log(f"   ▶ {Path(chemin).name}\n")


