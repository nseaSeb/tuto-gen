"""Panneau de paramètres : widgets, panels métier, dialogue réglages."""

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

from .. import _build_version, composer, config, paquet, settings, tts
from ..cli import _ouvrir, _slug, construire
from .common import (
    VERT, TL_LEFT, TL_TOP, TL_ROW_H, TL_GAP, TL_HANDLE, TL_MIN_BODY,
    SETTINGS_W, LIST_W, COUL, PRESETS_TEXTE, ROLE_LABEL,
    _to_float, _lighten, _parse_fin, _Q,
)


class PanelsMixin:
    # ======================================================== SETTINGS PANEL
    def _build_settings(self):
        for w in self.settings_inner.winfo_children():
            w.destroy()
        self.btn_ecoute = None
        self.btn_regen = None
        self._narr_dur_maj = None  # rafraîchisseur des durées (panneau narration)
        self._arrow_vars = {}
        self._hl_vars = {}
        self._timing_vars = {}
        self._title_pos_vars = {}
        self._texte_pos_vars = {}
        self._cap_pos_vars = {}
        self._scene_duree_var = None

        if self.current is None:
            self._sh("← Sélectionne une scène", fg="#444")
            return
        s = self.scenes[self.current]
        if self._sel is None or self._sel["kind"] == "scene_dur":
            self._panel_scene(s)
            return
        k, i = self._sel["kind"], self._sel["idx"]
        dispatch = {
            "narration": self._panel_narration,
            "capture": self._panel_capture,
            "arrow": self._panel_arrow,
            "highlight": self._panel_highlight,
            "sample": self._panel_sample,
            "texte": self._panel_texte,
        }
        fn = dispatch.get(k)
        if fn:
            fn(s, i)

    # ── Widget helpers ────────────────────────────────────────────────────
    def _stitle(self, txt: str):
        tk.Label(self.settings_inner, text=txt, bg="#141414", fg="#e0e0e0",
                 font=("Helvetica", 12, "bold"), anchor="w").pack(
            fill="x", padx=6, pady=(6, 2))

    def _sh(self, txt: str, fg: str = "#666"):
        tk.Label(self.settings_inner, text=txt, bg="#141414", fg=fg,
                 font=("Helvetica", 10), wraplength=SETTINGS_W - 20,
                 justify="left", anchor="w").pack(fill="x", padx=6, pady=3)

    def _ssep(self):
        tk.Frame(self.settings_inner, bg="#2a2a2a", height=1).pack(
            fill="x", padx=4, pady=5)

    def _srow(self, label: str) -> tk.Frame:
        row = tk.Frame(self.settings_inner, bg="#141414")
        row.pack(fill="x", padx=4, pady=3)
        tk.Label(row, text=label, bg="#141414", fg="#888",
                 font=("Helvetica", 10), width=13, anchor="w").pack(side="left")
        return row

    def _sentry(self, label: str, var: tk.Variable, w: int = 12) -> ttk.Entry:
        row = self._srow(label)
        e = ttk.Entry(row, textvariable=var, width=w)
        e.pack(side="left", fill="x", expand=True)
        return e

    def _sscale(self, label: str, var: tk.Variable,
                 lo: float = 0.0, hi: float = 1.0, res: float = 0.01):
        row = self._srow(label)
        inner = tk.Frame(row, bg="#141414")
        inner.pack(side="left", fill="x", expand=True)
        ttk.Scale(inner, variable=var, from_=lo, to=hi).pack(
            side="left", fill="x", expand=True)
        fmt = "%.0f" if res >= 1 else "%.2f"
        lv = tk.Label(inner, bg="#141414", fg="#aaa", font=("Menlo", 9), width=5)
        lv.pack(side="left")
        def _u(*_):
            lv.config(text=fmt % var.get())
        var.trace_add("write", _u)
        _u()

    def _scolor(self, label: str, getter, setter):
        row = self._srow(label)
        # tk.Button utilise le rendu natif Aqua sur macOS qui ignore `bg` (carré
        # blanc) : on passe par un Label, qui lui respecte la couleur de fond.
        sw = tk.Label(row, bg=getter(), width=4, relief="groove",
                      borderwidth=2, cursor="hand2")
        def _pick(*_):
            c = colorchooser.askcolor(color=getter(), title="Couleur")
            if c and c[1]:
                setter(c[1])
                sw.config(bg=c[1])
        sw.bind("<Button-1>", _pick)
        sw.pack(side="left", padx=4)

    def _s2fields(self, label: str, la: str, va: tk.Variable,
                   lb: str, vb: tk.Variable):
        row = self._srow(label)
        for l, v in ((la, va), (lb, vb)):
            tk.Label(row, text=l, bg="#141414", fg="#666",
                     font=("Helvetica", 9)).pack(side="left", padx=(4, 0))
            ttk.Entry(row, textvariable=v, width=7).pack(side="left", padx=(0, 4))

    def _timing_section(self, debut_s: float, fin_s: float | None, on_change,
                        fin_editable: bool = True):
        """Champs Début/Fin communs à tous les blocs.

        `fin_editable=False` (narration) : la durée est imposée par l'audio, le
        champ Fin est verrouillé."""
        self._ssep()
        tk.Label(self.settings_inner, text="Timing", bg="#141414", fg="#aaa",
                 font=("Helvetica", 10, "bold")).pack(anchor="w", padx=6)
        dv = tk.StringVar(value=f"{debut_s:.2f}")
        fv = tk.StringVar(value=f"{fin_s:.2f}" if fin_s is not None else "fin")
        row = tk.Frame(self.settings_inner, bg="#141414")
        row.pack(fill="x", padx=4, pady=3)
        # Début : Spinbox à pas fin (0.05 s) pour caler la synchro entre pistes ;
        # la saisie directe reste possible (précision 0.01 s).
        tk.Label(row, text="Début (s) :", bg="#141414", fg="#888",
                 font=("Helvetica", 10)).pack(side="left")
        ttk.Spinbox(row, textvariable=dv, width=7, from_=0.0, to=99999.0,
                    increment=0.05, format="%.2f").pack(side="left", padx=(0, 8))
        tk.Label(row, text="Fin (s) :", bg="#141414", fg="#888",
                 font=("Helvetica", 10)).pack(side="left")
        e_fin = ttk.Entry(row, textvariable=fv, width=7)
        e_fin.pack(side="left", padx=(0, 8))
        if not fin_editable:
            e_fin.config(state="disabled")
        note = ('durée = audio · ←/→ (Maj = fin) pour ajuster le début'
                if not fin_editable else
                '"fin" = jusqu\'à la fin de la scène · ←/→ (Maj = fin) '
                'pour ajuster le début dans la timeline')
        tk.Label(self.settings_inner, text=note,
                 bg="#141414", fg="#444", font=("Helvetica", 8),
                 wraplength=SETTINGS_W - 20, justify="left").pack(
            anchor="w", padx=6, pady=(0, 2))
        def _cb(*_):
            if not self._chargement:
                on_change(dv, fv)
                self._draw_timeline()
        dv.trace_add("write", _cb)
        fv.trace_add("write", _cb)
        self._timing_vars = {"debut": dv, "fin": fv}

    def _sdel(self):
        self._ssep()
        ttk.Button(self.settings_inner, text="🗑  Supprimer ce bloc",
                   command=self._del_selected).pack(anchor="w", padx=6, pady=4)

    # ── Boîte de dialogue « Réglages » du projet ────────────────────────────
    def _ouvrir_reglages(self):
        if self._reglages_win is not None and self._reglages_win.winfo_exists():
            self._reglages_win.lift()
            return
        win = tk.Toplevel(self.root)
        self._reglages_win = win
        win.title("Réglages du projet")
        win.geometry("520x620")
        win.transient(self.root)
        win.resizable(False, True)

        def _on_close():
            self.btn_fond = None
            self.btn_fond2 = None
            self.btn_st_fond = None
            self.lbl_fond_img = None
            self._fond_box = None
            self.voix_combo = None
            self.lbl_logo = None
            self.lbl_samples = None
            self.lbl_police = None
            self._reglages_win = None
            try:
                self._scroll_canvases.remove(canvas)
            except ValueError:
                pass
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _on_close)

        # Conteneur défilant : le contenu peut dépasser la hauteur visible de la
        # fenêtre (sinon le bas — n° de version, bouton Fermer — reste caché).
        # Le canvas est enregistré dans _scroll_canvases pour que le routeur de
        # molette/trackpad global (_wheel_router) le prenne en charge.
        outer = ttk.Frame(win)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0,
                           background=win.cget("background"))
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self._scroll_canvases.append(canvas)

        frm = ttk.Frame(canvas, padding=14)
        frm.columnconfigure(1, weight=1)
        _frm_id = canvas.create_window((0, 0), window=frm, anchor="nw")
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(_frm_id, width=e.width))
        frm.bind("<Configure>",
                 lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        ttk.Label(frm, text="Titre :").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(frm, textvariable=self.titre_var).grid(
            row=0, column=1, columnspan=2, sticky="we", pady=5)

        ttk.Label(frm, text="Application :").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(frm, textvariable=self.app_var).grid(
            row=1, column=1, columnspan=2, sticky="we", pady=5)

        ttk.Label(frm, text="Fond :").grid(row=2, column=0, sticky="nw", pady=5)
        fond_box = ttk.Frame(frm)
        fond_box.grid(row=2, column=1, columnspan=2, sticky="we", pady=5)
        self._build_fond_controls(fond_box)

        ttk.Label(frm, text="Logo :").grid(row=3, column=0, sticky="w", pady=5)
        logo_row = ttk.Frame(frm)
        logo_row.grid(row=3, column=1, columnspan=2, sticky="we", pady=5)
        ttk.Button(logo_row, text="Choisir une image…",
                   command=self._choisir_logo).pack(side="left")
        ttk.Button(logo_row, text="✕ Auto", width=8,
                   command=self._effacer_logo).pack(side="left", padx=(6, 0))
        self.lbl_logo = ttk.Label(frm, text="", foreground="#888")
        self.lbl_logo.grid(row=4, column=1, columnspan=2, sticky="w")
        self._maj_lbl_logo()

        ttk.Label(frm, text="Dossier samples :").grid(row=5, column=0, sticky="w", pady=5)
        s_row = ttk.Frame(frm)
        s_row.grid(row=5, column=1, columnspan=2, sticky="we", pady=5)
        ttk.Button(s_row, text="Choisir…",
                   command=self._choisir_dossier_samples).pack(side="left")
        ttk.Button(s_row, text="Ouvrir",
                   command=lambda: _ouvrir(settings.dossier_samples(self.reglages))
                   ).pack(side="left", padx=(6, 0))
        self.lbl_samples = ttk.Label(frm, text="", foreground="#888",
                                     wraplength=420)
        self.lbl_samples.grid(row=6, column=1, columnspan=2, sticky="w")
        self._maj_lbl_samples()

        ttk.Label(frm, text="Police :").grid(row=7, column=0, sticky="w", pady=5)
        p_row = ttk.Frame(frm)
        p_row.grid(row=7, column=1, columnspan=2, sticky="we", pady=5)
        ttk.Button(p_row, text="Choisir une police…",
                   command=self._choisir_police).pack(side="left")
        ttk.Button(p_row, text="✕ Défaut", width=9,
                   command=self._effacer_police).pack(side="left", padx=(6, 0))
        self.lbl_police = ttk.Label(frm, text="", foreground="#888", wraplength=420)
        self.lbl_police.grid(row=8, column=1, columnspan=2, sticky="w")
        self._maj_lbl_police()

        ttk.Label(frm, text="Taille de base (%) :").grid(
            row=9, column=0, sticky="w", pady=5)
        tb_row = ttk.Frame(frm)
        tb_row.grid(row=9, column=1, columnspan=2, sticky="w", pady=5)
        self._taille_base_var = tk.DoubleVar(
            value=round(getattr(self.reglages, "taille_base", 3.8), 1))
        ttk.Scale(tb_row, from_=1.5, to=12.0, variable=self._taille_base_var,
                  length=150).pack(side="left")
        sp = ttk.Spinbox(tb_row, from_=1.5, to=12.0, increment=0.1, width=6,
                         textvariable=self._taille_base_var, format="%.1f")
        sp.pack(side="left", padx=(8, 0))
        ttk.Label(frm, text="Titre / sous-titre / paragraphe = ×2.0 / ×1.15 / "
                  "×0.85 de cette taille.", foreground="#888",
                  wraplength=320).grid(row=10, column=1, columnspan=2, sticky="w")
        def _tb(*_):
            try:
                val = float(self._taille_base_var.get())
            except (tk.TclError, ValueError):
                return
            val = max(1.5, min(12.0, val))
            self.reglages.taille_base = val
            self.meta.taille_base = val
            settings.sauver(self.reglages)
            self._plan_apercu()
        self._taille_base_var.trace_add("write", _tb)

        # Bande de sous-titres : couleur de fond + opacité (transparence).
        ttk.Label(frm, text="Fond sous-titres :").grid(
            row=11, column=0, sticky="w", pady=5)
        st_row = ttk.Frame(frm)
        st_row.grid(row=11, column=1, columnspan=2, sticky="w", pady=5)
        # tk.Button ignore `bg` sous macOS (rendu natif Aqua → carré blanc) :
        # on utilise un Label, qui respecte la couleur de fond.
        self.btn_st_fond = tk.Label(
            st_row, bg=self.meta.sous_titre_fond, width=4, relief="groove",
            borderwidth=2, cursor="hand2")
        self.btn_st_fond.bind("<Button-1>",
                              lambda *_: self._choisir_sous_titre_fond())
        self.btn_st_fond.pack(side="left")
        self._st_opac_var = tk.DoubleVar(
            value=round(getattr(self.reglages, "sous_titre_fond_opacite", 0.55), 2))
        ttk.Label(st_row, text="opacité").pack(side="left", padx=(10, 4))
        ttk.Scale(st_row, from_=0.0, to=1.0, variable=self._st_opac_var,
                  length=120).pack(side="left")
        ttk.Spinbox(st_row, from_=0.0, to=1.0, increment=0.05, width=5,
                    textvariable=self._st_opac_var, format="%.2f").pack(
            side="left", padx=(6, 0))
        def _st_opac(*_):
            try:
                o = max(0.0, min(1.0, float(self._st_opac_var.get())))
            except (tk.TclError, ValueError):
                return
            self.meta.sous_titre_fond_opacite = o
            self.reglages.sous_titre_fond_opacite = o
            settings.sauver(self.reglages)
            self._plan_apercu()
        self._st_opac_var.trace_add("write", _st_opac)
        ttk.Label(frm, text="Opacité 0 = transparent, 1 = plein. Ex. : noir + "
                  "0,5 = bande translucide.", foreground="#888",
                  wraplength=320).grid(row=12, column=1, columnspan=2, sticky="w")

        ttk.Separator(frm).grid(row=13, column=0, columnspan=3, sticky="we", pady=10)
        ttk.Label(frm, text="Réglages visuels mémorisés et réappliqués aux "
                  "nouveaux projets. Pour la voix, voir « 🎙 Audio ».",
                  foreground="#888", wraplength=420).grid(
            row=14, column=0, columnspan=3, sticky="w")
        ttk.Label(frm, text=f"Version {_build_version.BUILD_VERSION}",
                  foreground="#888").grid(
            row=15, column=0, sticky="w", pady=(12, 0))
        ttk.Button(frm, text="Fermer", command=_on_close).grid(
            row=15, column=2, sticky="e", pady=(12, 0))

    # ── État / installation du moteur vocal (modèle XTTS) ───────────────────
    def _maj_etat_moteur(self):
        """Rafraîchit le libellé d'état et l'affichage du bouton d'installation.

        Pas d'import lourd : l'état repose sur la présence du modèle sur disque
        (embarqué ou déjà téléchargé)."""
        lbl = getattr(self, "_lbl_moteur", None)
        if lbl is None or not lbl.winfo_exists():
            return
        etat = tts.etat_moteur()
        if etat["pret"]:
            suffixe = " (embarquée)" if etat["embarque"] else ""
            lbl.config(text=f"✅ Voix installée{suffixe} — prête à l'emploi.",
                       foreground="#7fbf7f")
            self._btn_moteur.grid_remove()
            self._pb_moteur.grid_remove()
        else:
            go = etat["octets_total"] / 1e9
            lbl.config(text=f"⬇ Voix non installée — téléchargement unique "
                            f"d'environ {go:.1f} Go requis (connexion Internet).",
                       foreground="#d0b070")
            self._pb_moteur.grid_remove()
            self._btn_moteur.config(state="normal")
            self._btn_moteur.grid()

    def _installer_moteur(self):
        """Lance le téléchargement du modèle XTTS en arrière-plan + progression."""
        th = getattr(self, "_moteur_dl_thread", None)
        if th is not None and th.is_alive():
            return
        self._btn_moteur.config(state="disabled")
        self._pb_moteur.grid()
        self._pb_moteur["value"] = 0
        erreur: dict = {}

        def worker():
            try:
                tts.telecharger_modele()
            except Exception as e:  # réseau indispo, etc.
                erreur["e"] = e

        self._moteur_dl_thread = threading.Thread(target=worker, daemon=True)
        self._moteur_dl_thread.start()
        self._suivre_install_moteur(erreur)

    def _suivre_install_moteur(self, erreur: dict):
        """Poll périodique : met à jour la barre, puis l'état une fois fini."""
        lbl = getattr(self, "_lbl_moteur", None)
        if lbl is None or not lbl.winfo_exists():
            return  # dialogue fermé pendant le téléchargement
        total = tts.XTTS_TAILLE_TOTALE or 1
        octets = tts.taille_modele_cache()
        pct = max(0, min(100, int(octets * 100 / total)))
        th = getattr(self, "_moteur_dl_thread", None)
        if th is not None and th.is_alive():
            self._pb_moteur["value"] = pct
            etiq = "Finalisation…" if pct >= 99 else f"{pct}%"
            lbl.config(
                text=f"⬇ Téléchargement de la voix… {etiq}  "
                     f"({octets / 1e9:.2f} / {total / 1e9:.1f} Go)",
                foreground="#d0b070")
            self.root.after(500, lambda: self._suivre_install_moteur(erreur))
            return
        # Terminé
        if erreur.get("e") is not None:
            lbl.config(text=f"⚠ Échec du téléchargement : {erreur['e']}. "
                            "Vérifiez la connexion puis réessayez.",
                       foreground="#e08a8a")
            self._pb_moteur.grid_remove()
            self._btn_moteur.config(state="normal")
            self._btn_moteur.grid()
        else:
            self._pb_moteur["value"] = 100
            self._maj_etat_moteur()

    # ── Boîte de dialogue « Audio » (moteur de voix XTTS) ───────────────────
    def _ouvrir_reglages_audio(self):
        if (self._reglages_audio_win is not None
                and self._reglages_audio_win.winfo_exists()):
            self._reglages_audio_win.lift()
            return
        win = tk.Toplevel(self.root)
        self._reglages_audio_win = win
        win.title("Réglages audio (voix XTTS)")
        win.geometry("560x720")
        win.transient(self.root)
        win.resizable(False, False)

        def _on_close():
            self.lbl_voix_ref = None
            self._dico_inner = None
            self._dico_rows = []
            self._lbl_moteur = None
            self._reglages_audio_win = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _on_close)

        # Section « Moteur vocal » : état (installé / à télécharger) + barre de
        # progression du téléchargement initial du modèle (~1,9 Go). Packée au
        # dessus du formulaire pour ne pas décaler la grille existante.
        entete = ttk.Frame(win, padding=(14, 12, 14, 0))
        entete.pack(fill="x")
        entete.columnconfigure(0, weight=1)
        self._lbl_moteur = ttk.Label(entete, text="", wraplength=420)
        self._lbl_moteur.grid(row=0, column=0, sticky="w")
        self._btn_moteur = ttk.Button(entete, text="⬇ Installer la voix",
                                       command=self._installer_moteur)
        self._btn_moteur.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self._pb_moteur = ttk.Progressbar(entete, mode="determinate",
                                          maximum=100, length=420)
        self._pb_moteur.grid(row=1, column=0, columnspan=2, sticky="we",
                             pady=(6, 0))
        ttk.Separator(entete).grid(row=2, column=0, columnspan=2,
                                   sticky="we", pady=(12, 0))
        self._maj_etat_moteur()

        frm = ttk.Frame(win, padding=14)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        # Voix de référence : clonage XTTS d'un WAV (prioritaire sur le speaker).
        ttk.Label(frm, text="Voix (WAV) :").grid(row=0, column=0, sticky="w", pady=5)
        vr_row = ttk.Frame(frm)
        vr_row.grid(row=0, column=1, columnspan=2, sticky="we", pady=5)
        ttk.Button(vr_row, text="Choisir un WAV…",
                   command=self._choisir_voix_reference).pack(side="left")
        ttk.Button(vr_row, text="✕ Défaut", width=9,
                   command=self._effacer_voix_reference).pack(side="left", padx=(6, 0))
        self.lbl_voix_ref = ttk.Label(vr_row, text="", foreground="#888")
        self.lbl_voix_ref.pack(side="left", padx=(8, 0))
        self._maj_lbl_voix_ref()
        ttk.Label(frm, text="Échantillon FR propre (5-10 s) cloné par XTTS. "
                  "Vide = voix intégrée ci-dessous.", foreground="#888",
                  wraplength=340).grid(row=1, column=1, columnspan=2, sticky="w")

        # Voix intégrée XTTS (ignorée si un WAV de référence est défini).
        ttk.Label(frm, text="Voix intégrée :").grid(
            row=2, column=0, sticky="w", pady=5)
        self._speaker_var = tk.StringVar(
            value=getattr(self.reglages, "voix_speaker", "") or "(défaut)")
        cb_spk = ttk.Combobox(frm, textvariable=self._speaker_var,
                              state="readonly", width=28,
                              values=("(défaut)",) + tts.XTTS_SPEAKERS)
        cb_spk.grid(row=2, column=1, columnspan=2, sticky="w", pady=5)
        if self.meta.voix_reference:
            cb_spk.config(state="disabled")  # le clone d'un WAV prime

        def _maj_speaker(*_):
            spk = self._speaker_var.get()
            spk = "" if spk == "(défaut)" else spk
            self.meta.voix_speaker = self.reglages.voix_speaker = spk
            settings.sauver(self.reglages)
        self._speaker_var.trace_add("write", _maj_speaker)
        ttk.Label(frm, text="Speaker studio XTTS si aucun WAV. Aucun n'a "
                  "l'accent FR natif : un WAV reste préférable.",
                  foreground="#888", wraplength=340).grid(
            row=3, column=1, columnspan=2, sticky="w")

        # Paramètres de voix XTTS : vitesse / expressivité / fluidité.
        self._voix_vitesse_var = tk.DoubleVar(
            value=round(getattr(self.reglages, "voix_vitesse", 1.0), 2))
        self._voix_expr_var = tk.DoubleVar(
            value=round(getattr(self.reglages, "voix_expressivite", 0.75), 2))
        self._voix_fluidite_var = tk.BooleanVar(
            value=bool(getattr(self.reglages, "voix_fluidite", False)))

        def _maj_voix_params(*_):
            try:
                v = max(0.7, min(1.3, float(self._voix_vitesse_var.get())))
                e = max(0.4, min(0.95, float(self._voix_expr_var.get())))
            except (tk.TclError, ValueError):
                return
            f = bool(self._voix_fluidite_var.get())
            self.meta.voix_vitesse = self.reglages.voix_vitesse = v
            self.meta.voix_expressivite = self.reglages.voix_expressivite = e
            self.meta.voix_fluidite = self.reglages.voix_fluidite = f
            settings.sauver(self.reglages)

        ttk.Label(frm, text="Vitesse :").grid(row=4, column=0, sticky="w", pady=5)
        v_row = ttk.Frame(frm)
        v_row.grid(row=4, column=1, columnspan=2, sticky="w", pady=5)
        ttk.Scale(v_row, from_=0.7, to=1.3, variable=self._voix_vitesse_var,
                  length=150).pack(side="left")
        ttk.Spinbox(v_row, from_=0.7, to=1.3, increment=0.05, width=6,
                    textvariable=self._voix_vitesse_var, format="%.2f").pack(
            side="left", padx=(8, 0))

        ttk.Label(frm, text="Expressivité :").grid(row=5, column=0, sticky="w", pady=5)
        e_row = ttk.Frame(frm)
        e_row.grid(row=5, column=1, columnspan=2, sticky="w", pady=5)
        ttk.Scale(e_row, from_=0.4, to=0.95, variable=self._voix_expr_var,
                  length=150).pack(side="left")
        ttk.Spinbox(e_row, from_=0.4, to=0.95, increment=0.05, width=6,
                    textvariable=self._voix_expr_var, format="%.2f").pack(
            side="left", padx=(8, 0))

        ttk.Checkbutton(frm, text="Fluidité (synthèse phrase par phrase)",
                        variable=self._voix_fluidite_var).grid(
            row=6, column=1, columnspan=2, sticky="w", pady=(2, 0))
        for _v in (self._voix_vitesse_var, self._voix_expr_var,
                   self._voix_fluidite_var):
            _v.trace_add("write", _maj_voix_params)

        # Dictionnaire de prononciation : tableau (mot / prononciation + test/suppr).
        ttk.Label(frm, text="Prononciation :").grid(
            row=7, column=0, sticky="nw", pady=5)
        dico_box = ttk.Frame(frm)
        dico_box.grid(row=7, column=1, columnspan=2, sticky="we", pady=5)
        head = ttk.Frame(dico_box)
        head.pack(fill="x")
        ttk.Label(head, text="Mot", foreground="#888", width=15).pack(
            side="left", padx=(0, 4))
        ttk.Label(head, text="Prononciation", foreground="#888").pack(side="left")
        body = ttk.Frame(dico_box)
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, height=120, highlightthickness=0, bg="#141414")
        vsb = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        self._dico_inner = ttk.Frame(canvas)
        self._dico_inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self._dico_inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self._dico_rows = []
        for k, v in (getattr(self.reglages, "prononciations", {}) or {}).items():
            self._dico_ajouter_ligne(k, v)

        ttk.Button(frm, text="＋ Ajouter un mot",
                   command=lambda: self._dico_ajouter_ligne()).grid(
            row=8, column=1, sticky="w", pady=(2, 0))
        ttk.Label(frm, text="« 🔊 » génère un audio de test du mot · « ✕ » le "
                  "retire.", foreground="#888", wraplength=340).grid(
            row=9, column=1, columnspan=2, sticky="w")

        ttk.Separator(frm).grid(row=10, column=0, columnspan=3, sticky="we", pady=10)
        ttk.Label(frm, text="Ces réglages voix sont mémorisés et appliqués à "
                  "tous les projets (y compris à la réouverture). "
                  "Vitesse/expressivité peuvent être surchargées par narration.",
                  foreground="#888",
                  wraplength=420).grid(row=11, column=0, columnspan=3, sticky="w")
        ttk.Button(frm, text="Fermer", command=_on_close).grid(
            row=12, column=2, sticky="e", pady=(12, 0))

    # ── Tableau du dictionnaire de prononciation ────────────────────────────
    def _dico_ajouter_ligne(self, mot: str = "", prononciation: str = ""):
        """Ajoute une ligne (mot, prononciation, test, suppression) au tableau."""
        row = ttk.Frame(self._dico_inner)
        row.pack(fill="x", pady=1)
        mv = tk.StringVar(value=mot)
        pv = tk.StringVar(value=prononciation)
        entry = {"frame": row, "mot": mv, "prono": pv}
        ttk.Entry(row, textvariable=mv, width=15).pack(side="left", padx=(0, 4))
        ttk.Entry(row, textvariable=pv, width=16).pack(side="left", padx=(0, 4))
        ttk.Button(row, text="🔊", width=3,
                   command=lambda: self._dico_tester(mv)).pack(side="left")
        ttk.Button(row, text="✕", width=3,
                   command=lambda: self._dico_supprimer(entry)).pack(
            side="left", padx=(2, 0))
        self._dico_rows.append(entry)
        for v in (mv, pv):
            v.trace_add("write", lambda *_: self._dico_collecter())
        if mot or prononciation:
            self._dico_collecter()  # persiste une ligne pré-remplie

    def _dico_collecter(self):
        """Relit le tableau et persiste le dictionnaire (meta + réglages)."""
        dico = {}
        for r in self._dico_rows:
            k = r["mot"].get().strip()
            if k:
                dico[k] = r["prono"].get().strip()
        self.meta.prononciations = dico
        self.reglages.prononciations = dico
        settings.sauver(self.reglages)

    def _dico_supprimer(self, entry):
        try:
            entry["frame"].destroy()
        except Exception:
            pass
        if entry in self._dico_rows:
            self._dico_rows.remove(entry)
        self._dico_collecter()

    def _dico_tester(self, mot_var):
        """Synthétise une courte phrase contenant `mot` (règles appliquées) et
        la joue, pour valider à l'oreille la prononciation choisie."""
        mot = mot_var.get().strip()
        if not mot:
            self._log("   (saisis d'abord un mot à tester)\n")
            return
        self._dico_collecter()  # applique les règles courantes (édition non sauvée)
        phrase = f"Voici comment se prononce : {mot}."
        params = config.params_voix(self.meta)
        voix = self.meta.voix
        self._log(f"\n🔊 Test de prononciation : « {mot} »…\n")

        def work():
            from .. import tts
            self._avertir_telechargement_modele()
            try:
                tmp = Path(tempfile.mkdtemp(prefix="tutoprono_"))
                clip = tts.synthetiser(phrase, voix, tmp / "prono.wav", **params)
                if clip:
                    self.q.put(f"   ✓ {clip.duree:.1f}s — lecture…\n")
                    subprocess.run(["afplay", str(clip.chemin)], check=False)
                else:
                    self.q.put("   (rien à synthétiser)\n")
            except Exception as e:
                self.q.put(f"   ✗ test impossible : {e}\n")
        threading.Thread(target=work, daemon=True).start()

    # ── Panels métier ─────────────────────────────────────────────────────
    def _panel_scene(self, s: config.Scene):
        self._stitle("Slide titre" if s.type == "title" else "Slide capture")
        self._ssep()
        tv = tk.StringVar(value=s.titre)
        self._sentry("Titre :", tv)
        def _t(*_):
            if self._chargement:
                return
            s.titre = tv.get()
            if self.current is not None and self.current < len(self.scene_rows):
                self.scene_rows[self.current]["lbl"].config(
                    text=self._lbl_scene(s))
            self._plan_apercu()
        tv.trace_add("write", _t)

        # Sous-titre + positions : disponibles sur tous les types de slide.
        sv = tk.StringVar(value=s.sous_titre)
        self._sentry("Sous-titre :", sv)
        def _s(*_):
            if not self._chargement:
                s.sous_titre = sv.get()
                self._plan_apercu()
        sv.trace_add("write", _s)

        # Position du titre / sous-titre (centre du bloc, en % de la slide)
        self._ssep()
        self._sh("Position (% de la slide) — ou glisse le texte "
                 "directement sur l'aperçu.", fg="#555")
        tx = tk.StringVar(value=f"{s.titre_x:.0f}")
        ty = tk.StringVar(value=f"{s.titre_y:.0f}")
        sx = tk.StringVar(value=f"{s.sous_titre_x:.0f}")
        sy = tk.StringVar(value=f"{s.sous_titre_y:.0f}")
        self._s2fields("Titre", "x", tx, "y", ty)
        self._s2fields("Sous-titre", "x", sx, "y", sy)
        def _pos(*_):
            if self._chargement:
                return
            s.titre_x = max(0.0, min(100.0, _to_float(tx.get(), 50.0)))
            s.titre_y = max(0.0, min(100.0, _to_float(ty.get(), 53.0)))
            s.sous_titre_x = max(0.0, min(100.0, _to_float(sx.get(), 50.0)))
            s.sous_titre_y = max(0.0, min(100.0, _to_float(sy.get(), 62.0)))
            self._plan_apercu()
        for v in (tx, ty, sx, sy):
            v.trace_add("write", _pos)
        self._title_pos_vars = {"tx": tx, "ty": ty, "sx": sx, "sy": sy}

        if s.type in ("title", "screenshot"):
            # Logo : position + zoom (ou glisser-déposer sur l'aperçu)
            self._ssep()
            tk.Label(self.settings_inner, text="Logo", bg="#141414", fg="#aaa",
                     font=("Helvetica", 10, "bold")).pack(anchor="w", padx=6)
            lx = tk.StringVar(value=f"{s.logo_x:.0f}")
            ly = tk.StringVar(value=f"{s.logo_y:.0f}")
            self._s2fields("Position", "x", lx, "y", ly)
            lz = tk.DoubleVar(value=round(s.logo_echelle, 0))
            self._sscale("Zoom (%) :", lz, lo=20, hi=999, res=5)
            def _logo(*_):
                if self._chargement:
                    return
                s.logo_x = max(0.0, min(100.0, _to_float(lx.get(), 50.0)))
                s.logo_y = max(0.0, min(100.0, _to_float(ly.get(), 31.0)))
                s.logo_echelle = max(20.0, min(999.0, float(lz.get())))
                self._plan_apercu()
            for v in (lx, ly, lz):
                v.trace_add("write", _logo)

        dv = tk.StringVar(value=str(s.duree_min or "5"))
        self._scene_duree_var = dv
        self._sentry("Durée (s) :", dv, w=7)
        def _d(*_):
            if not self._chargement:
                s.duree_min = max(0.5, _to_float(dv.get(), 5.0))
                self._draw_timeline()
        dv.trace_add("write", _d)

        if s.type == "title":
            self._ssep()
            self._sh("Une narration peut être ajoutée à une slide titre "
                     "via « + Narration » dans la timeline.", fg="#555")
        else:
            self._ssep()
            self._sh("Ajoute narrations, captures, flèches, highlights et "
                     "samples depuis la barre de la timeline, puis clique un "
                     "bloc pour l'éditer.", fg="#555")

    def _panel_narration(self, s: config.Scene, idx: int):
        if idx >= len(s.narrations):
            return
        n = s.narrations[idx]
        # À la sélection : on garantit que la durée de la piste = durée de
        # l'audio (pas de « fin » résiduelle, début borné, slide allongée si
        # besoin). Sans effet tant que l'audio n'est pas généré.
        self._normaliser_narration(n)
        self._preview_t = n.debut
        self._stitle(f"Narration {idx + 1}")
        self._ssep()
        txt = tk.Text(self.settings_inner, height=8, wrap="word",
                      font=("Helvetica", 11), bg="#1e1e1e", fg="#e0e0e0",
                      insertbackground="white", relief="flat", padx=6, pady=6)
        txt.pack(fill="x", padx=4, pady=2)
        txt.insert("1.0", n.texte)
        def _m(*_):
            if not self._chargement:
                n.texte = txt.get("1.0", "end").strip()
                self._plan_apercu()
        txt.bind("<KeyRelease>", _m)
        # Indicateur d'état du cache audio pour cette narration.
        cache_lbl = tk.Label(self.settings_inner, text="", bg="#141414",
                             fg="#888", font=("Helvetica", 9), anchor="w")
        cache_lbl.pack(fill="x", padx=6)
        self._cache_lbl = cache_lbl
        self._maj_cache_status(n, cache_lbl)
        # Durées : audio généré vs longueur de la piste dans la timeline. Les
        # afficher côte à côte facilite l'ajustement du timing (caler « fin »
        # sur la durée audio, ou allonger la scène).
        dur_lbl = tk.Label(self.settings_inner, text="", bg="#141414",
                           fg="#7a9bbf", font=("Menlo", 9), anchor="w")
        dur_lbl.pack(fill="x", padx=6)

        def _maj_dur():
            if not dur_lbl.winfo_exists():
                return
            audio, piste = self._narr_durees_info(n)
            a = f"{audio:.1f}s" if audio is not None else "— (non généré)"
            dur_lbl.config(text=f"⏱  audio {a}   ·   piste {piste:.1f}s")
        self._narr_dur_maj = _maj_dur
        _maj_dur()
        # Au « blur » (perte de focus), on pré-génère l'audio en cache : la
        # génération de la vidéo réutilisera ainsi un audio déjà prêt.
        def _blur(_=None):
            if self._chargement:
                return
            n.texte = txt.get("1.0", "end").strip()
            self._prechauffer_narration(n, cache_lbl)
        txt.bind("<FocusOut>", _blur)
        btn_row = tk.Frame(self.settings_inner, bg="#141414")
        btn_row.pack(anchor="w", padx=6, pady=6)
        self.btn_ecoute = ttk.Button(btn_row, text="🔊  Écouter",
                                      command=lambda: self._ecouter(n))
        self.btn_ecoute.pack(side="left")
        self.btn_regen = ttk.Button(
            btn_row, text="🔄  Régénérer",
            command=lambda: self._regenerer_narration(n, cache_lbl))
        self.btn_regen.pack(side="left", padx=(6, 0))
        # Panneau reconstruit pendant une synthèse : recrée les boutons grisés.
        if getattr(self, "_tts_busy", False):
            self.btn_ecoute.config(state="disabled")
            self.btn_regen.config(state="disabled")
        self._sh("« Régénérer » force une nouvelle prise (voix légèrement "
                 "différente).", fg="#555")
        self._sh("La durée audio détermine la fin si « fin » est laissé.",
                 fg="#555")

        # --- Surcharges voix par segment (None = hérite des réglages) ---------
        self._ssep()
        gv, ge = self.meta.voix_vitesse, self.meta.voix_expressivite
        seg_v = tk.DoubleVar(value=n.vitesse if n.vitesse is not None else gv)
        seg_e = tk.DoubleVar(
            value=n.expressivite if n.expressivite is not None else ge)
        perso = tk.BooleanVar(
            value=(n.vitesse is not None or n.expressivite is not None))

        def _seg_apply(*_):
            if self._chargement:
                return
            if perso.get():
                n.vitesse = round(float(seg_v.get()), 2)
                n.expressivite = round(float(seg_e.get()), 2)
            else:
                n.vitesse = n.expressivite = None
            self._maj_cache_status(n, cache_lbl)
            if self._narr_dur_maj:
                self._narr_dur_maj()

        ttk.Checkbutton(self.settings_inner,
                        text="Vitesse / expressivité propres à ce segment",
                        variable=perso, command=_seg_apply).pack(anchor="w", padx=6)
        self._sscale("Vitesse :", seg_v, lo=0.7, hi=1.3, res=0.01)
        self._sscale("Expressivité :", seg_e, lo=0.3, hi=1.0, res=0.01)
        for _v in (seg_v, seg_e):
            _v.trace_add("write", _seg_apply)

        # --- Sous-titre : affichage on/off + texte distinct de la narration ---
        self._ssep()
        tk.Label(self.settings_inner, text="Sous-titre", bg="#141414", fg="#aaa",
                 font=("Helvetica", 10, "bold")).pack(anchor="w", padx=6)
        st_on = tk.BooleanVar(value=n.afficher_sous_titre)
        st_txt = tk.Text(self.settings_inner, height=2, wrap="word",
                         font=("Helvetica", 11), bg="#1e1e1e", fg="#e0e0e0",
                         insertbackground="white", relief="flat", padx=6, pady=6)

        def _st(*_):
            if self._chargement:
                return
            n.afficher_sous_titre = bool(st_on.get())
            n.sous_titre = st_txt.get("1.0", "end").strip()
            st_txt.config(state="normal" if st_on.get() else "disabled")
            self._plan_apercu()

        ttk.Checkbutton(self.settings_inner, text="Afficher le sous-titre",
                        variable=st_on, command=_st).pack(anchor="w", padx=6)
        st_txt.pack(fill="x", padx=4, pady=2)
        st_txt.insert("1.0", n.sous_titre)
        st_txt.bind("<KeyRelease>", _st)
        self._sh("Laisser vide = même texte que la narration.", fg="#555")
        st_txt.config(state="normal" if n.afficher_sous_titre else "disabled")

        def _timing_cb(dv, fv):
            # Durée imposée par l'audio : seule le début est ajustable.
            self._poser_debut("narration", n, _to_float(dv.get()))
            n.fin = None
            self._sync_timing_vars(n.debut, n.fin)
            self._preview_t = n.debut
            _maj_dur()
        self._timing_section(n.debut, None, _timing_cb, fin_editable=False)
        self._sdel()

    def _panel_capture(self, s: config.Scene, idx: int):
        if idx >= len(s.captures):
            return
        cap = s.captures[idx]
        self._preview_t = cap.debut
        self._stitle(f"Capture {idx + 1}")
        self._ssep()
        sv = tk.StringVar(value=str(cap.chemin or ""))
        self._sentry("Fichier :", sv)
        def _m(*_):
            if not self._chargement:
                p = sv.get().strip()
                cap.chemin = Path(p) if p else None
                self._plan_apercu()
        sv.trace_add("write", _m)
        ttk.Button(self.settings_inner, text="Parcourir…",
                   command=lambda: self._choisir_shot(sv)).pack(
            anchor="w", padx=6, pady=4)

        # Position (décalage %) + zoom — ou glisser la capture sur l'aperçu
        self._ssep()
        self._sh("Glisse la capture sur l'aperçu pour la déplacer.", fg="#555")
        cx = tk.StringVar(value=f"{cap.decalage_x:.0f}")
        cy = tk.StringVar(value=f"{cap.decalage_y:.0f}")
        self._s2fields("Décalage (%)", "x", cx, "y", cy)
        self._cap_pos_vars = {"x": cx, "y": cy}
        cz = tk.DoubleVar(value=round(cap.echelle, 0))
        self._sscale("Zoom (%) :", cz, lo=20, hi=400, res=5)
        def _pos(*_):
            if self._chargement:
                return
            cap.decalage_x = max(-100.0, min(100.0, _to_float(cx.get(), 0.0)))
            cap.decalage_y = max(-100.0, min(100.0, _to_float(cy.get(), 0.0)))
            cap.echelle = max(20.0, min(400.0, float(cz.get())))
            self._plan_apercu()
        for v in (cx, cy, cz):
            v.trace_add("write", _pos)

        def _timing_cb(dv, fv):
            cap.debut = max(0.0, _to_float(dv.get()))
            cap.fin = _parse_fin(fv.get())
            self._preview_t = cap.debut
        self._timing_section(cap.debut, cap.fin, _timing_cb)
        self._sdel()

    def _panel_arrow(self, s: config.Scene, idx: int):
        if idx >= len(s.annotations):
            return
        a = s.annotations[idx]
        self._preview_t = a.debut
        n = sum(1 for x in s.annotations[:idx] if x.type == "arrow") + 1
        self._stitle(f"Flèche {n}")
        self._sh("Drag sur l'aperçu pour déplacer la flèche", fg="#555")
        self._ssep()
        de = a.de or (20.0, 25.0)
        vs = a.vers or (60.0, 55.0)
        dx = tk.StringVar(value=f"{de[0]:.1f}")
        dy = tk.StringVar(value=f"{de[1]:.1f}")
        vx = tk.StringVar(value=f"{vs[0]:.1f}")
        vy = tk.StringVar(value=f"{vs[1]:.1f}")
        self._s2fields("Départ (%)", "x", dx, "y", dy)
        self._s2fields("Arrivée (%)", "x", vx, "y", vy)

        # Style de flèche
        row = self._srow("Style :")
        style_var = tk.StringVar(value=getattr(a, "style", "Fleche1"))
        ttk.Combobox(row, textvariable=style_var, width=18, state="readonly",
                     values=list(config.STYLES_FLECHE)).pack(side="left")

        tv = tk.IntVar(value=getattr(a, "taille", 100))
        self._sscale("Taille (%) :", tv, lo=20, hi=400, res=5)
        rot = tk.IntVar(value=int(round(getattr(a, "rotation", 0.0))))
        self._sscale("Rotation (°) :", rot, lo=-180, hi=180, res=1)
        self._scolor("Couleur :", lambda: a.couleur,
                     lambda c: (setattr(a, "couleur", c),
                                self._plan_apercu(), self._draw_timeline()))

        def _m(*_):
            if not self._chargement:
                a.de = (_to_float(dx.get()), _to_float(dy.get()))
                a.vers = (_to_float(vx.get()), _to_float(vy.get()))
                a.taille = max(20, min(400, int(tv.get())))
                a.rotation = max(-180, min(180, int(rot.get())))
                a.style = style_var.get()
                self._memo_taille_fleche(a.style, a.taille)
                self._plan_apercu()
        for v in (dx, dy, vx, vy, tv, rot):
            v.trace_add("write", _m)

        def _on_style(*_):
            if self._chargement:
                return
            st = style_var.get()
            # Applique la taille mémorisée pour ce style (réglage idéal retrouvé).
            memo = self.reglages.tailles_fleche.get(st)
            if memo is not None and int(tv.get()) != int(memo):
                self._chargement = True
                tv.set(int(memo))
                self._chargement = False
            a.style = st
            a.taille = max(20, min(400, int(tv.get())))
            self._memo_taille_fleche(st, a.taille)
            self._plan_apercu()
            self._draw_timeline()
        style_var.trace_add("write", _on_style)
        self._arrow_vars = {"dx": dx, "dy": dy, "vx": vx, "vy": vy}

        def _timing_cb(dv, fv):
            a.debut = max(0.0, _to_float(dv.get()))
            a.fin = _parse_fin(fv.get())
            self._preview_t = a.debut
        self._timing_section(a.debut, a.fin, _timing_cb)
        self._sdel()

    def _panel_highlight(self, s: config.Scene, idx: int):
        if idx >= len(s.annotations):
            return
        a = s.annotations[idx]
        self._preview_t = a.debut
        n = sum(1 for x in s.annotations[:idx] if x.type == "highlight") + 1
        self._stitle(f"Highlight {n}")
        self._sh("Drag sur l'aperçu pour déplacer la zone", fg="#555")
        self._ssep()
        z = a.zone or (20.0, 20.0, 60.0, 60.0)
        x1 = tk.StringVar(value=f"{z[0]:.1f}")
        y1 = tk.StringVar(value=f"{z[1]:.1f}")
        x2 = tk.StringVar(value=f"{z[2]:.1f}")
        y2 = tk.StringVar(value=f"{z[3]:.1f}")
        op = tk.DoubleVar(value=round(a.opacite, 2))
        self._s2fields("Coin H-G (%)", "x", x1, "y", y1)
        self._s2fields("Coin B-D (%)", "x", x2, "y", y2)
        self._sscale("Opacité :", op, lo=0.0, hi=1.0, res=0.01)
        self._scolor("Couleur :", lambda: a.couleur,
                     lambda c: (setattr(a, "couleur", c),
                                self._plan_apercu(), self._draw_timeline()))
        def _m(*_):
            if not self._chargement:
                a.zone = (_to_float(x1.get()), _to_float(y1.get()),
                          _to_float(x2.get()), _to_float(y2.get()))
                a.opacite = round(op.get(), 2)
                self._plan_apercu()
        for v in (x1, y1, x2, y2, op):
            v.trace_add("write", _m)
        self._hl_vars = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}

        def _timing_cb(dv, fv):
            a.debut = max(0.0, _to_float(dv.get()))
            a.fin = _parse_fin(fv.get())
            self._preview_t = a.debut
        self._timing_section(a.debut, a.fin, _timing_cb)
        self._sdel()

    def _panel_sample(self, s: config.Scene, idx: int):
        if idx >= len(s.samples):
            return
        sa = s.samples[idx]
        self._preview_t = sa.debut
        self._stitle(f"Sample {idx + 1}")
        self._ssep()

        # Fichier courant + écoute
        nm = Path(sa.chemin).name if sa.chemin else "(aucun fichier)"
        lbl_nm = tk.Label(self.settings_inner, text=nm, bg="#141414",
                          fg="#bbb" if sa.chemin else "#777",
                          font=("Menlo", 9), wraplength=SETTINGS_W - 24,
                          anchor="w")
        lbl_nm.pack(fill="x", padx=6, pady=2)
        ttk.Button(self.settings_inner, text="▶  Écouter",
                   command=lambda: self._play_sample(sa.chemin)).pack(
            anchor="w", padx=6, pady=(0, 4))

        # Recherche dans la bibliothèque
        self._sh("Bibliothèque : samples livrés (★) + ton dossier perso "
                 "(réglable dans ⚙ Réglages).", fg="#666")
        rech = tk.StringVar()
        self._sentry("Rechercher :", rech)
        liste = tk.Listbox(self.settings_inner, height=6, activestyle="none",
                           bg="#1e1e1e", fg="#ddd", selectbackground=VERT,
                           relief="flat", font=("Helvetica", 10),
                           exportselection=False)
        liste.pack(fill="x", padx=6, pady=2)

        biblio = settings.lister_samples(self.reglages)

        def _remplir(*_):
            q = rech.get().strip().lower()
            liste.delete(0, "end")
            self._sample_biblio_vis = [
                p for p in biblio
                if not q or q in settings.label_sample(self.reglages, p).lower()
            ]
            for p in self._sample_biblio_vis:
                liste.insert("end", f"🎵 {settings.label_sample(self.reglages, p)}")
            if not biblio:
                liste.insert("end", "(dossier vide — choisis-le dans ⚙ Réglages)")
        rech.trace_add("write", _remplir)
        _remplir()

        def _assigner(chemin: Path):
            # Le picker fournit déjà un chemin de bibliothèque stable : livré
            # (assets/samples, résolu par nom à l'ouverture) ou perso
            # (~/.tuto-gen/samples). On ne copie rien ici : les livrés sont
            # embarqués dans l'app, les persos sont rassemblés à l'enregistrement.
            chemin = Path(chemin)
            sa.chemin = chemin
            self._sample_durees.pop(str(chemin), None)
            lbl_nm.config(text=Path(chemin).name, fg="#bbb")
            self._draw_timeline()
            self._build_settings()  # rafraîchit durée/affichage

        def _selection():
            sel = liste.curselection()
            vis = getattr(self, "_sample_biblio_vis", [])
            if sel and sel[0] < len(vis):
                return vis[sel[0]]
            return None

        def _on_pick(_=None):
            p = _selection()
            if p is not None:
                _assigner(p)

        def _on_preview(_=None):
            # Écoute à la sélection (simple clic), sans assigner
            p = _selection()
            if p is not None:
                self._play_sample(p)
        liste.bind("<<ListboxSelect>>", _on_preview)
        liste.bind("<Double-Button-1>", _on_pick)

        brow = tk.Frame(self.settings_inner, bg="#141414")
        brow.pack(fill="x", padx=6, pady=2)
        ttk.Button(brow, text="▶ Écouter", command=_on_preview).pack(side="left")
        ttk.Button(brow, text="Assigner", command=_on_pick).pack(side="left", padx=4)
        ttk.Button(brow, text="📂 Importer…",
                   command=lambda: self._importer_sample_pour(sa, lbl_nm)).pack(
            side="left", padx=4)
        self._sh("Clic = écoute · double-clic / Assigner = choisir", fg="#555")

        vol = tk.DoubleVar(value=round(sa.volume, 2))
        self._sscale("Volume :", vol, lo=0.0, hi=1.0, res=0.01)
        def _mv(*_):
            if not self._chargement:
                sa.volume = round(vol.get(), 2)
        vol.trace_add("write", _mv)
        if sa.chemin:
            self._sh(f"Durée fichier : {self._sample_dur(sa.chemin):.2f}s", fg="#666")

        def _timing_cb(dv, fv):
            sa.fin = _parse_fin(fv.get())
            if sa.fin is None and self._span_verrou("sample", sa) is not None:
                # durée verrouillée sur le fichier audio : on borne le début
                self._poser_debut("sample", sa, _to_float(dv.get()))
                self._sync_timing_vars(sa.debut, sa.fin)
            else:
                sa.debut = max(0.0, _to_float(dv.get()))
            self._preview_t = sa.debut
        self._timing_section(sa.debut, sa.fin, _timing_cb)
        self._sdel()

    def _importer_sample_pour(self, sa: config.SampleAudio, lbl):
        """Choisit un fichier (hors bibliothèque) et l'assigne au sample."""
        f = filedialog.askopenfilename(
            title="Choisir / télécharger un sample audio",
            filetypes=[("Audio", "*.wav *.mp3 *.aiff *.aif *.m4a *.ogg *.flac"),
                       ("Tous", "*.*")])
        if not f:
            return
        # Proposer de copier dans la bibliothèque pour réutilisation
        dest = settings.dossier_samples(self.reglages)
        if messagebox.askyesno(
                "Bibliothèque",
                f"Copier « {Path(f).name} » dans la bibliothèque\n{dest} ?\n\n"
                "(Oui = réutilisable plus tard ; Non = lien direct au fichier)"):
            import shutil
            try:
                dest.mkdir(parents=True, exist_ok=True)
                cible = dest / Path(f).name
                shutil.copy2(f, cible)
                f = str(cible)
            except Exception as e:
                self._log(f"   ⚠ copie impossible : {e}\n")
        f = self._adopter(f)
        sa.chemin = Path(f)
        self._sample_durees.pop(str(f), None)
        if lbl and lbl.winfo_exists():
            lbl.config(text=Path(f).name, fg="#bbb")
        self._draw_timeline()
        self._build_settings()

    def _panel_texte(self, s: config.Scene, idx: int):
        if idx >= len(s.textes):
            return
        tx = s.textes[idx]
        self._preview_t = tx.debut
        self._stitle(f"Texte {idx + 1}")
        self._sh("Glisse le bloc sur l'aperçu pour le positionner.", fg="#555")
        self._ssep()

        # Contenu
        txt = tk.Text(self.settings_inner, height=4, wrap="word",
                      font=("Helvetica", 11), bg="#1e1e1e", fg="#e0e0e0",
                      insertbackground="white", relief="flat", padx=6, pady=6)
        txt.pack(fill="x", padx=4, pady=2)
        txt.insert("1.0", tx.texte)

        # Style : Titre / Sous-titre / Paragraphe (taille dérivée de la base)
        # ou Personnalisé (taille libre via le slider).
        row = self._srow("Style :")
        style_var = tk.StringVar(value=ROLE_LABEL.get(tx.role, "Personnalisé"))
        ttk.Combobox(row, textvariable=style_var, width=14, state="readonly",
                     values=list(PRESETS_TEXTE.keys()) + ["Personnalisé"]
                     ).pack(side="left")

        taille = tk.DoubleVar(value=round(composer.taille_effective(self.meta, tx), 1))
        self._sscale("Taille (%) :", taille, lo=1.5, hi=15.0, res=0.1)
        gras = tk.BooleanVar(value=tx.gras)
        rowg = self._srow("Gras :")
        ttk.Checkbutton(rowg, variable=gras).pack(side="left")
        align = tk.StringVar(value=tx.align)
        rowa = self._srow("Alignement :")
        ttk.Combobox(rowa, textvariable=align, width=10, state="readonly",
                     values=["left", "center", "right"]).pack(side="left")
        larg = tk.DoubleVar(value=round(tx.largeur, 0))
        self._sscale("Largeur (%) :", larg, lo=10, hi=100, res=1)
        self._scolor("Couleur :", lambda: tx.couleur,
                     lambda c: (setattr(tx, "couleur", c), self._plan_apercu()))

        # Position (synchronisée avec le drag)
        px = tk.StringVar(value=f"{tx.x:.0f}")
        py = tk.StringVar(value=f"{tx.y:.0f}")
        self._s2fields("Position (%)", "x", px, "y", py)
        self._texte_pos_vars = {"x": px, "y": py}

        def _maj(*_):
            if self._chargement:
                return
            tx.texte = txt.get("1.0", "end").strip()
            tx.gras = bool(gras.get())
            tx.align = align.get()
            tx.largeur = round(float(larg.get()), 0)
            tx.x = max(0.0, min(100.0, _to_float(px.get(), tx.x)))
            tx.y = max(0.0, min(100.0, _to_float(py.get(), tx.y)))
            self._plan_apercu()
            self._draw_timeline()
        txt.bind("<KeyRelease>", _maj)
        for v in (gras, align, larg, px, py):
            v.trace_add("write", _maj)

        def _on_taille(*_):
            # Modifier la taille manuellement => style Personnalisé.
            if self._chargement:
                return
            tx.role = "libre"
            tx.taille = round(float(taille.get()), 1)
            self._chargement = True
            style_var.set("Personnalisé")
            self._chargement = False
            self._plan_apercu()
        taille.trace_add("write", _on_taille)

        def _on_style(*_):
            if self._chargement:
                return
            disp = style_var.get()
            self._chargement = True
            try:
                if disp in PRESETS_TEXTE:
                    role, g_ = PRESETS_TEXTE[disp]
                    tx.role = role
                    tx.gras = g_
                    gras.set(g_)
                    taille.set(self._taille_role(role))  # affichage
                else:
                    tx.role = "libre"
                    tx.taille = round(float(taille.get()), 1)
            finally:
                self._chargement = False
            self._plan_apercu()
            self._draw_timeline()
        style_var.trace_add("write", _on_style)

        def _timing_cb(dv, fv):
            tx.debut = max(0.0, _to_float(dv.get()))
            tx.fin = _parse_fin(fv.get())
            self._preview_t = tx.debut
        self._timing_section(tx.debut, tx.fin, _timing_cb)
        self._sdel()


