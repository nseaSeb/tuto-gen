"""Projet : YAML, session, paquets, export MP3 et génération vidéo."""

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

from .. import composer, config, imaging, paquet, settings
from ..cli import _ouvrir, _slug, construire
from .common import (
    VERT, TL_LEFT, TL_TOP, TL_ROW_H, TL_GAP, TL_HANDLE, TL_MIN_BODY,
    SETTINGS_W, LIST_W, COUL, PRESETS_TEXTE, ROLE_LABEL,
    _to_float, _lighten, _parse_fin, _Q,
)


class ProjectMixin:
    # ============================================================= YAML
    def _charger_meta(self):
        # Met à jour les variables projet ; les visuels (fond/logo) sont
        # rafraîchis quand le panneau Projet est (re)construit.
        self._chargement = True
        self.titre_var.set(self.meta.titre)
        self.app_var.set(self.meta.app)
        self.voix_var.set(self.meta.voix)
        self._chargement = False

    # ── Vérification du moteur de voix (sous-processus isolé) ───────────────
    def _verifier_moteur(self):
        if self._moteur_verifie:
            return
        self._moteur_verifie = True
        import sys
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "selftest"]
        else:
            cmd = [sys.executable, "-m", "tuto_gen", "selftest"]

        def work():
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
                ok = (r.returncode == 0 and "ok" in (r.stdout or "").lower())
                detail = ((r.stdout or "") + (r.stderr or "")).strip()
            except Exception as e:
                ok, detail = False, str(e)
            self.q.put(("__MOTEUR__", ok, detail))
        threading.Thread(target=work, daemon=True).start()

    def _moteur_ko(self, detail: str):
        self._log("⚠ Le moteur de voix XTTS ne se charge pas sur cette machine.\n")
        if detail:
            self._log(f"   {detail[:300]}\n")
        messagebox.showwarning(
            "Voix XTTS indisponible",
            "Le moteur de synthèse vocale (XTTS) ne parvient pas à se charger.\n\n"
            "XTTS est inclus dans l'application : aucune installation n'est "
            "nécessaire. Sur une app non signée téléchargée, macOS bloque souvent "
            "ses bibliothèques internes même après avoir « autorisé » l'app.\n\n"
            "Solution (Terminal), à exécuter une fois sur le .app :\n"
            "    xattr -dr com.apple.quarantine /chemin/vers/tuto-gen.app\n\n"
            "Sans moteur vocal, la génération échouera (pas de voix de repli).")

    # ── Sauvegarde automatique / restauration ───────────────────────────────
    def _autosave(self):
        """Écrit le projet courant dans ~/.tuto-gen pour récupération."""
        if not self.scenes:
            return
        try:
            settings.assurer_dossiers()
            # base_dir = dossier d'autosave → chemins d'assets en absolu.
            cfg = config.Config(meta=self.meta, scenes=self.scenes,
                                base_dir=settings.DOSSIER)
            config.sauver(cfg, settings.AUTOSAVE)
            import json
            settings.SESSION.write_text(json.dumps({
                "current": self.current if self.current is not None else 0,
                "project_path": str(self.project_path) if self.project_path else None,
                "base_dir": str(self.base_dir),
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _autosave_tick(self):
        self._autosave()
        try:
            self.root.after(15000, self._autosave_tick)
        except Exception:
            pass

    def _on_quit(self):
        self._play_stop()
        self._autosave()
        try:
            self.root.destroy()
        except Exception:
            pass

    def _restaurer_session(self) -> bool:
        """Recharge le projet de la dernière session si présent. True si restauré."""
        import json
        if not (settings.AUTOSAVE.is_file() and settings.SESSION.is_file()):
            return False
        try:
            cfg = config.charger(settings.AUTOSAVE)
            st = json.loads(settings.SESSION.read_text(encoding="utf-8"))
        except Exception:
            return False
        if not cfg.scenes:
            return False
        self.meta = cfg.meta
        # Réglages voix globaux (popup 🎙) = source de vérité : on les réapplique
        # au projet restauré (sinon ils ne prenaient effet qu'après ouverture de
        # la popup).
        self._appliquer_reglages_voix(self.meta)
        self.scenes = cfg.scenes
        bd = st.get("base_dir")
        self.base_dir = Path(bd) if bd else Path.home()
        pp = st.get("project_path")
        self.project_path = Path(pp) if pp else None
        self._reparer_samples_manquants()
        self.current = min(max(0, int(st.get("current", 0))), len(self.scenes) - 1)
        self._sel = None
        self._charger_meta()
        self._refresh_liste(self.current)
        self._select_scene(self.current)
        self._log("↩︎ Session précédente restaurée "
                  "(menu Nouveau pour repartir de zéro).\n")
        return True

    def _nouveau(self, confirmer=True):
        if confirmer and self.scenes and not messagebox.askyesno(
                "Nouveau projet", "Abandonner le projet courant ?"):
            return
        self.meta = self._meta_par_defaut()
        self.scenes = [
            config.Scene(id="intro", type="title", titre="Mon tutoriel",
                         sous_titre="Sous-titre", duree_min=3.0),
            self._nouvelle_scene("screenshot"),
        ]
        self.scenes[1].id = "etape_1"
        self.scenes[1].titre = "Première étape"
        self.base_dir = Path.home()
        self.project_path = None
        self.current = 0
        self._sel = None
        self._charger_meta()
        self._refresh_liste(0)
        self._select_scene(0)

    def _ouvrir_yaml(self):
        f = filedialog.askopenfilename(
            title="Ouvrir un tuto.yaml",
            filetypes=[("YAML", "*.yaml *.yml"), ("Tous", "*.*")])
        if not f:
            return
        try:
            cfg = config.charger(f)
        except Exception as e:
            messagebox.showerror("YAML invalide", str(e))
            return
        self._charger_projet(cfg, f)

    def _charger_projet(self, cfg: config.Config, chemin):
        """Installe un `Config` chargé dans l'état de l'éditeur."""
        self.meta = cfg.meta
        # Réglages voix globaux (popup 🎙) = source de vérité : ils priment sur
        # ceux figés dans le projet ouvert.
        self._appliquer_reglages_voix(self.meta)
        self.scenes = cfg.scenes
        self.base_dir = cfg.base_dir
        self.project_path = Path(chemin)
        self._reparer_samples_manquants()
        self.current = 0 if self.scenes else None
        self._sel = None
        self._charger_meta()
        self._refresh_liste(self.current)
        self._select_scene(self.current or 0)

    def _exporter_paquet(self):
        f = filedialog.asksaveasfilename(
            title="Exporter un paquet .tuto", defaultextension=".tuto",
            initialfile=f"{_slug(self.meta.titre)}.tuto",
            filetypes=[("Paquet tuto", "*.tuto")])
        if not f:
            return
        cfg = config.Config(meta=self.meta, scenes=self.scenes,
                            base_dir=self.base_dir)
        self._log("\n📦 Export du paquet (médias + audio)…\n")

        def worker():
            try:
                paquet.exporter(cfg, f, log=lambda m: self.q.put(m + "\n"))
            except Exception as e:
                self.q.put(f"   ✗ Export impossible : {e}\n")
        threading.Thread(target=worker, daemon=True).start()

    def _importer_paquet(self):
        f = filedialog.askopenfilename(
            title="Importer un paquet .tuto",
            filetypes=[("Paquet tuto", "*.tuto"), ("Tous", "*.*")])
        if not f:
            return
        d = filedialog.askdirectory(title="Dossier où extraire le paquet")
        if not d:
            return
        self._log("\n📥 Import du paquet…\n")

        def worker():
            try:
                cfg, yaml_path = paquet.importer(
                    f, d, log=lambda m: self.q.put(m + "\n"))
                self.q.put(("__IMPORT_DONE__", cfg, yaml_path))
            except Exception as e:
                self.q.put(f"   ✗ Import impossible : {e}\n")
        threading.Thread(target=worker, daemon=True).start()

    def _media_dir(self) -> Path | None:
        """Dossier `media/` du projet (à côté du YAML), ou None si non enregistré."""
        return Path(self.project_path).parent / "media" if self.project_path else None

    def _adopter(self, chemin) -> str:
        """Copie un fichier choisi dans le dossier média du projet (s'il existe).

        Modèle « projet autonome » : tant que le projet n'a pas été enregistré,
        le fichier reste référencé en place ; le premier enregistrement
        rassemblera tout. Renvoie le chemin (absolu) à utiliser."""
        md = self._media_dir()
        if not md or not chemin:
            return str(chemin)
        try:
            return str(paquet.adopter_fichier(md, chemin))
        except Exception as e:
            self._log(f"   ⚠ copie dans le projet impossible : {e}\n")
            return str(chemin)

    def _chemin_sample_stable(self, chemin) -> str:
        """Pérennise le chemin d'un sample pris dans la bibliothèque livrée (★).

        Les samples livrés résident sous `sys._MEIPASS/assets/samples`, un
        chemin volatile : il change à chaque build et, sur macOS, à chaque
        lancement de l'app non signée (App Translocation). Le référencer
        directement fait « disparaître » le son à la relecture comme à la
        génération (le fichier n'existe plus à ce chemin → piste ignorée).
        On copie donc le fichier dans la bibliothèque utilisateur
        (~/.tuto-gen/samples), stable et inscriptible, et on renvoie ce
        chemin pérenne. Les autres sources sont renvoyées telles quelles."""
        livres = settings.samples_livres()
        if not (livres and chemin):
            return str(chemin)
        try:
            Path(chemin).resolve().relative_to(Path(livres).resolve())
        except (ValueError, OSError):
            return str(chemin)  # pas un sample livré → rien à pérenniser
        try:
            dest = settings.dossier_samples(self.reglages)
            return str(paquet.adopter_fichier(dest, chemin))
        except Exception as e:
            self._log(f"   ⚠ copie du sample livré impossible : {e}\n")
            return str(chemin)

    def _reparer_samples_manquants(self):
        """Re-cible les samples dont le fichier a disparu vers un même nom
        retrouvé dans la bibliothèque (livrés ★ + dossier perso).

        Soigne les projets enregistrés avec un ancien chemin volatile de
        sample livré (cf. `_chemin_sample_stable`) : sans cela, le son reste
        muet jusqu'à réaffectation manuelle. Sans effet si rien n'est cassé.

        La correspondance se fait par nom de fichier. En cas d'homonymie
        (plusieurs candidats pour un même nom), on s'abstient de deviner :
        relier au mauvais bruitage en silence serait plus déroutant qu'un
        trou franc. On signale alors l'ambiguïté pour réaffectation manuelle."""
        biblio = None
        repares = ambigus = introuvables = 0
        for s in self.scenes:
            for sa in s.samples:
                if not sa.chemin or Path(sa.chemin).is_file():
                    continue
                if biblio is None:
                    biblio = {}
                    for p in settings.lister_samples(self.reglages):
                        biblio.setdefault(p.name, []).append(p)
                candidats = biblio.get(Path(sa.chemin).name, [])
                if len(candidats) == 1:
                    neuf = Path(self._chemin_sample_stable(candidats[0]))
                    sa.chemin = neuf
                    self._sample_durees.pop(str(neuf), None)
                    repares += 1
                elif len(candidats) > 1:
                    ambigus += 1
                else:
                    introuvables += 1
        if repares:
            self._log(f"   ↺ {repares} sample(s) manquant(s) re-localisé(s) "
                      "depuis la bibliothèque.\n")
        if ambigus:
            self._log(f"   ⚠ {ambigus} sample(s) à nom ambigu (plusieurs "
                      "correspondances) — à réaffecter dans le panneau Sample.\n")
        if introuvables:
            self._log(f"   ⚠ {introuvables} sample(s) introuvable(s) dans la "
                      "bibliothèque — à réaffecter dans le panneau Sample.\n")

    def _enregistrer_yaml(self):
        f = filedialog.asksaveasfilename(
            title="Enregistrer le projet (dossier autonome)",
            defaultextension=".yaml",
            initialfile=f"{_slug(self.meta.titre)}.yaml",
            filetypes=[("YAML", "*.yaml")])
        if not f:
            return
        dossier = Path(f).parent
        cfg = config.Config(meta=self.meta, scenes=self.scenes,
                            base_dir=self.base_dir)
        try:
            # Rassemble tous les assets dans <dossier>/media, puis écrit le
            # YAML en chemins relatifs.
            cfg, n_medias, manquants = paquet.collecter(
                cfg, dossier / "media", log=lambda m: self._log(m + "\n"))
            config.sauver(cfg, f)
        except Exception as e:
            messagebox.showerror("Erreur", str(e))
            return
        # L'éditeur travaille désormais sur les copies locales du dossier.
        cfg.base_dir = dossier
        self._charger_projet(cfg, f)
        detail = f"{n_medias} média(s) rassemblé(s)"
        if manquants:
            detail += f", {manquants} introuvable(s)"
        self._log(f"\n💾 Projet enregistré : {f} ({detail})\n")

    def _choisir_shot(self, var: tk.StringVar):
        f = filedialog.askopenfilename(
            title="Choisir un screenshot",
            filetypes=imaging.motif_filetypes())
        if f:
            var.set(self._adopter(f))


    # ===================================================== EXPORT MP3
    def _exporter_mp3(self):
        narr = [s for s in self.scenes if s.a_narration()]
        if not narr:
            messagebox.showinfo("Rien à exporter",
                                "Aucune scène ne contient de narration.")
            return
        base = self.project_path.parent if self.project_path else self.base_dir
        dossier = filedialog.askdirectory(
            title="Dossier d'export des MP3", initialdir=str(base))
        if not dossier:
            return
        self._log("\n" + "═" * 54 + "\n")
        self._log("🎙  Export des narrations en MP3…\n")
        threading.Thread(target=self._worker_mp3,
                         args=(list(self.scenes), Path(dossier), self.meta),
                         daemon=True).start()

    def _worker_mp3(self, scenes, dossier: Path, meta):
        from .. import tts
        n = 0
        try:
            for i, s in enumerate(scenes, start=1):
                for j, narr in enumerate(s.narrations):
                    phrase = (narr.texte or "").strip()
                    if not phrase:
                        continue
                    suff = f"_{j + 1}" if len(s.narrations) > 1 else ""
                    nom = f"{i:02d}_{_slug(s.titre or s.id)}{suff}.mp3"
                    out = dossier / nom
                    self.q.put(f"   • {nom} …\n")
                    clip = tts.exporter_mp3(phrase, meta.voix, out,
                                            **config.params_voix(meta, narr))
                    if clip:
                        n += 1
                        self.q.put(f"     ✓ {clip.duree:.1f}s\n")
        except Exception as e:
            self.q.put(f"   ✗ {e}\n")
        self.q.put(("__MP3__", str(dossier), n))

    # =========================================================== GÉNÉRATION
    def _generer(self):
        if not self.scenes:
            messagebox.showwarning("Projet vide", "Ajoute au moins une scène.")
            return
        base = self.project_path.parent if self.project_path else self.base_dir
        sortie = base / "output" / f"{_slug(self.meta.titre)}.mp4"
        cfg = config.Config(meta=self.meta, scenes=self.scenes, base_dir=base)
        self.btn_gen.config(state="disabled")
        self.btn_open.config(state="disabled")
        self.video_path = None
        self.prog.start(12)
        self._log("\n" + "═" * 54 + "\n")
        threading.Thread(target=self._worker,
                         args=(cfg, sortie), daemon=True).start()

    def _worker(self, cfg, sortie):
        ecr = _Q(self.q)
        path = None
        self._avertir_telechargement_modele()
        try:
            with redirect_stdout(ecr):
                path = construire(cfg, Path(sortie))
        except Exception as e:
            self.q.put(f"\n✗ {e}\n")
        self.q.put(("__DONE__", str(path) if path else None))

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if isinstance(item, tuple) and item:
                    if item[0] == "__DONE__":
                        self._fin(item[1])
                    elif item[0] in ("__ECOUTE__", "__REGEN__"):
                        self._set_tts_busy(False)
                    elif item[0] == "__MP3__":
                        _, dossier, n = item
                        self._log(f"✅ {n} fichier(s) MP3 exporté(s).\n")
                        if n:
                            _ouvrir(Path(dossier))
                    elif item[0] == "__MOTEUR__":
                        _, ok, detail = item
                        if ok:
                            self._log("✓ Moteur de voix XTTS opérationnel.\n")
                        else:
                            self._moteur_ko(detail)
                    elif item[0] == "__PLAY_READY__":
                        if self._play_intent:
                            self._play_start(item[1])
                    elif item[0] == "__IMPORT_DONE__":
                        self._charger_projet(item[1], item[2])
                        self._log("✓ Paquet chargé dans l'éditeur.\n")
                else:
                    self._log(item)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _log(self, s: str):
        self.log.config(state="normal")
        self.log.insert("end", s)
        self.log.see("end")
        self.log.config(state="disabled")

    def _fin(self, path: str | None):
        self.prog.stop()
        self.btn_gen.config(state="normal")
        if path and Path(path).is_file():
            self.video_path = path
            self.btn_open.config(state="normal")
            self._ouvrir_video()
        else:
            messagebox.showerror("Échec", "La génération a échoué. Voir le journal.")

    def _ouvrir_video(self):
        if self.video_path:
            _ouvrir(Path(self.video_path))

