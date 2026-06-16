"""Éditeur visuel tuto-gen — assemblage de l'interface.

Layout :
  - barre d'outils + méta projet (haut)
  - 3 colonnes : liste scènes | aperçu interactif | panneau paramètres
  - timeline auto-hauteur : pistes nommées (narrations, captures, flèches,
    highlights, samples) avec blocs déplaçables/redimensionnables
  - bas : génération + journal

Modèle « sous-séquences » : plusieurs narrations et captures par scène,
chacune avec début/fin. Clic dans la timeline = scrub de l'aperçu.
"""

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
from .panels import PanelsMixin
from .apercu import ApercuMixin
from .timeline import TimelineMixin
from .playback import PlaybackMixin
from .project import ProjectMixin


class Editor(PanelsMixin, ApercuMixin, TimelineMixin,
             PlaybackMixin, ProjectMixin):
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("tuto-gen — éditeur")
        root.geometry("1380x980")
        root.minsize(1100, 800)

        self.reglages = settings.charger()
        self.meta = self._meta_par_defaut()
        self.scenes: list[config.Scene] = []
        self.base_dir = Path.home()
        self.project_path: Path | None = None
        self.current: int | None = None
        self.video_path: str | None = None

        self.q: queue.Queue = queue.Queue()
        self._chargement = False
        self._apercu_job = None
        self._tk_img = None
        self._apercu_sig = None        # signature de l'état visible rendu
        self._preview_t = 0.0          # instant rendu dans l'aperçu

        # Bloc sélectionné dans la timeline
        self._sel: dict | None = None        # {"kind": ..., "idx": ...}
        self._tl_drag: dict | None = None    # état de drag
        self._tl_cw = 0
        self._tl_drawing = False
        self._mode = "timeline"

        # Drag aperçu
        self._drag_anno: dict | None = None
        self._shot_box: tuple | None = None
        self._slide_disp: tuple | None = None   # (ix, iy, ratio) de l'aperçu
        self._free_drag: dict | None = None     # {"kind","idx"} en drag libre
        self._cap_drag: dict | None = None      # déplacement d'une capture
        self._drag_zoom: dict | None = None      # déplacement d'une zone de zoom
        self._base_cache: tuple | None = None    # (clé, image base) avant zoom
        self._title_pos_vars: dict = {}
        self._texte_pos_vars: dict = {}
        self._cap_pos_vars: dict = {}
        self._move_cursor_name: str | None = None  # curseur « déplacer » résolu

        # Vars de sync inter-widgets
        self._scene_duree_var: tk.StringVar | None = None
        self._arrow_vars: dict = {}
        self._hl_vars: dict = {}
        self._timing_vars: dict = {}

        self._sample_durees: dict = {}
        self._narr_durees: dict = {}  # durée audio (cache) par (texte+voix)
        self.btn_ecoute: ttk.Button | None = None
        self.btn_regen: ttk.Button | None = None
        self._tts_busy = False  # une synthèse (écoute/régén) est en cours

        # Lecture (Play) de l'aperçu — déroulé du tuto avec son
        self._playing = False
        self._play_mode = "tuto"            # "tuto" (tout) ou "slide" (scène courante)
        self._play_intent = False           # l'utilisateur veut lire (prépa en cours)
        self._play_job = None               # id du after() de la boucle
        self._play_t0 = 0.0                 # horloge de départ (monotonic)
        self._play_schedule = None          # {"scenes":[...], "audio":[...], "total":..}
        self._play_audio: list = []         # process afplay en cours
        self._play_cache: dict = {}         # (texte, ref) -> ClipAudio

        self._build_ui()
        if not self._restaurer_session():
            self._nouveau(confirmer=False)
        self.root.after(100, self._poll)
        # Sauvegarde automatique périodique + à la fermeture.
        self.root.after(15000, self._autosave_tick)
        try:
            self.root.protocol("WM_DELETE_WINDOW", self._on_quit)
        except Exception:
            pass
        # Vérifie le moteur de voix sans risquer de planter l'app.
        self._moteur_verifie = False
        self.root.after(400, self._verifier_moteur)

    def _appliquer_reglages_voix(self, meta: config.Meta) -> config.Meta:
        """Force les réglages voix globaux (popup 🎙) sur `meta`.

        Source de vérité unique : les réglages globaux priment sur ceux figés
        dans un projet chargé/restauré (sinon ils ne s'appliquaient qu'après
        ouverture de la popup). Les surcharges par narration
        (`Narration.vitesse`/`expressivite`) restent prioritaires au moment de
        la synthèse (cf. `config.params_voix`)."""
        r = self.reglages
        meta.voix = r.voix
        meta.voix_reference = (Path(r.voix_reference)
                               if getattr(r, "voix_reference", None)
                               and Path(r.voix_reference).is_file() else None)
        meta.voix_speaker = getattr(r, "voix_speaker", "")
        meta.voix_vitesse = getattr(r, "voix_vitesse", 1.0)
        meta.voix_expressivite = getattr(r, "voix_expressivite", 0.75)
        meta.voix_fluidite = getattr(r, "voix_fluidite", False)
        meta.prononciations = dict(getattr(r, "prononciations", {}) or {})
        return meta

    def _meta_par_defaut(self) -> config.Meta:
        r = self.reglages
        meta = config.Meta(
            titre="Mon tutoriel",
            logo=Path(r.logo) if r.logo and Path(r.logo).is_file() else None,
            couleur_fond=r.couleur_fond,
            couleur_accent=r.couleur_accent,
            fond_type=getattr(r, "fond_type", "couleur"),
            couleur_fond2=getattr(r, "couleur_fond2", "#1B4332"),
            degrade_sens=getattr(r, "degrade_sens", "vertical"),
            fond_image=(Path(r.fond_image)
                        if r.fond_image and Path(r.fond_image).is_file() else None),
            police=(Path(r.police)
                    if r.police and Path(r.police).is_file() else None),
            taille_base=getattr(r, "taille_base", 3.8),
            resolution=self._reglage_resolution(),
            sous_titre_fond=getattr(r, "sous_titre_fond", "#000000"),
            sous_titre_fond_opacite=getattr(r, "sous_titre_fond_opacite", 0.55),
        )
        return self._appliquer_reglages_voix(meta)

    def _taille_role(self, role: str) -> float:
        """Taille (% slide) d'un rôle, dérivée de la taille de base globale."""
        base = getattr(self.reglages, "taille_base", 3.8) or 3.8
        return round(base * composer.RATIOS_ROLE.get(role, 0.85), 1)

    # ================================================================ BUILD
    def _build_ui(self):
        # ── Barre d'outils ────────────────────────────────────────────────
        bar = tk.Frame(self.root, bg=VERT)
        bar.pack(fill="x")
        tk.Label(bar, text="🎬 tuto-gen", bg=VERT, fg="white",
                 font=("Helvetica", 14, "bold")).pack(side="left", padx=10, pady=6)
        for txt, cmd in [("Nouveau", self._nouveau),
                         ("Ouvrir .yaml…", self._ouvrir_yaml),
                         ("Enregistrer .yaml…", self._enregistrer_yaml),
                         ("📦 Exporter .tuto…", self._exporter_paquet),
                         ("📥 Importer .tuto…", self._importer_paquet)]:
            ttk.Button(bar, text=txt, command=cmd).pack(side="left", padx=3, pady=4)
        ttk.Button(bar, text="⚙  Réglages", command=self._ouvrir_reglages).pack(
            side="left", padx=(12, 3), pady=4)
        ttk.Button(bar, text="🎙  Audio", command=self._ouvrir_reglages_audio).pack(
            side="left", padx=3, pady=4)

        # Variables du projet (les widgets vivent dans la boîte « Réglages »)
        self.titre_var = tk.StringVar()
        self.app_var = tk.StringVar()
        self.voix_var = tk.StringVar()
        self.btn_fond = None
        self.btn_fond2 = None
        self.btn_st_fond = None
        self.lbl_fond_img = None
        self._fond_box = None
        self.voix_combo = None
        self.lbl_logo = None
        self.lbl_samples = None
        self.lbl_police = None
        self.lbl_voix_ref = None
        self._reglages_win = None
        self._reglages_audio_win = None
        for v in (self.titre_var, self.app_var):
            v.trace_add("write", self._maj_meta)
        self.voix_var.trace_add("write", self._maj_meta)

        # Canvas défilants enregistrés auprès du routeur molette (cf.
        # _init_molette) : liste des scènes, panneau de réglages, timeline.
        self._scroll_canvases = []

        # ── 3 colonnes ────────────────────────────────────────────────────
        corps = ttk.Frame(self.root)
        corps.pack(fill="both", expand=True, padx=8, pady=2)

        # Gauche : liste de scènes (lignes cliquables + ✕ pour supprimer)
        col_l = tk.Frame(corps, width=LIST_W, bg="#1c1c1c")
        col_l.pack(side="left", fill="y")
        col_l.pack_propagate(False)
        tk.Label(col_l, text="Scènes", bg="#1c1c1c", fg="#ccc",
                 font=("Helvetica", 11, "bold")).pack(anchor="w", padx=8, pady=(6, 2))

        # Barre du bas : ajout de slide + réorganisation (packée en premier
        # côté bas pour rester visible même quand la liste défile)
        bl = tk.Frame(col_l, bg="#1c1c1c")
        bl.pack(side="bottom", fill="x", padx=6, pady=6)
        ttk.Button(bl, text="＋ Slide titre",
                   command=lambda: self._ajouter("title")).grid(
            row=0, column=0, columnspan=2, padx=2, pady=2, sticky="we")
        ttk.Button(bl, text="＋ Slide capture",
                   command=lambda: self._ajouter("screenshot")).grid(
            row=1, column=0, columnspan=2, padx=2, pady=2, sticky="we")
        ttk.Button(bl, text="↑ Monter",
                   command=lambda: self._deplacer(-1)).grid(row=2, column=0, padx=2, pady=2, sticky="we")
        ttk.Button(bl, text="↓ Descendre",
                   command=lambda: self._deplacer(1)).grid(row=2, column=1, padx=2, pady=2, sticky="we")
        ttk.Button(bl, text="⧉ Dupliquer",
                   command=self._dupliquer).grid(row=3, column=0, columnspan=2, padx=2, pady=2, sticky="we")
        bl.columnconfigure(0, weight=1)
        bl.columnconfigure(1, weight=1)

        lc = tk.Canvas(col_l, bg="#161616", bd=0, highlightthickness=0)
        lsb = ttk.Scrollbar(col_l, orient="vertical", command=lc.yview)
        lc.configure(yscrollcommand=lsb.set)
        lsb.pack(side="right", fill="y")
        lc.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self.scene_list_frame = tk.Frame(lc, bg="#161616")
        lwin = lc.create_window((0, 0), window=self.scene_list_frame, anchor="nw")
        self.scene_list_frame.bind(
            "<Configure>",
            lambda e: (lc.configure(scrollregion=lc.bbox("all")),
                       lc.itemconfig(lwin, width=lc.winfo_width())))
        lc.bind("<Configure>", lambda e: lc.itemconfig(lwin, width=e.width))
        self._scroll_canvases.append(lc)
        self.scene_rows: list[dict] = []

        # Centre : aperçu
        col_c = ttk.Frame(corps)
        col_c.pack(side="left", fill="both", expand=True, padx=6)
        lh = tk.Frame(col_c, bg="#1a1a1a")
        lh.pack(fill="x")
        tk.Label(lh, text="Aperçu", bg="#1a1a1a", fg="#ccc",
                 font=("Helvetica", 11, "bold")).pack(side="left", pady=3)
        self.lbl_apercu_t = tk.Label(lh, text="t = 0.0s", bg="#1a1a1a",
                                     fg="#888", font=("Menlo", 9))
        self.lbl_apercu_t.pack(side="left", padx=8)
        self.btn_play = ttk.Button(lh, text="▶  Lire le tuto",
                                   command=self._play_toggle)
        self.btn_play.pack(side="right", padx=4, pady=2)
        self.btn_play_slide = ttk.Button(
            lh, text="▶  Lire la slide",
            command=lambda: self._play_toggle("slide"))
        self.btn_play_slide.pack(side="right", padx=2, pady=2)
        self.apercu_canvas = tk.Canvas(col_c, bg="#0d0d0d", bd=0,
                                       cursor="crosshair", highlightthickness=1,
                                       highlightbackground="#333")
        self.apercu_canvas.pack(fill="both", expand=True, pady=2)
        self.apercu_canvas.bind("<Button-1>", self._ap_down)
        self.apercu_canvas.bind("<B1-Motion>", self._ap_move)
        self.apercu_canvas.bind("<ButtonRelease-1>", self._ap_up)
        self.apercu_canvas.bind("<Motion>", self._ap_hover)
        self.apercu_canvas.bind("<Leave>",
                                lambda _: self.apercu_canvas.config(cursor="crosshair"))
        self.apercu_canvas.bind("<Configure>", lambda _: self._plan_apercu())

        # Droite : paramètres
        col_r = tk.Frame(corps, width=SETTINGS_W, bg="#141414")
        col_r.pack(side="left", fill="y")
        col_r.pack_propagate(False)
        tk.Label(col_r, text="Paramètres", bg="#141414", fg="#ccc",
                 font=("Helvetica", 11, "bold")).pack(anchor="w", padx=8, pady=(6, 2))
        sc = tk.Canvas(col_r, bg="#141414", bd=0, highlightthickness=0)
        sb = ttk.Scrollbar(col_r, orient="vertical", command=sc.yview)
        sc.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        sc.pack(side="left", fill="both", expand=True)
        self.settings_inner = tk.Frame(sc, bg="#141414")
        win = sc.create_window((0, 0), window=self.settings_inner, anchor="nw")
        self.settings_inner.bind(
            "<Configure>",
            lambda e: (sc.configure(scrollregion=sc.bbox("all")),
                       sc.itemconfig(win, width=sc.winfo_width())))
        sc.bind("<Configure>", lambda e: sc.itemconfig(win, width=e.width))
        self._scroll_canvases.append(sc)

        # ── Poignée de redimensionnement ───────────────────────────────────
        # Placée AU-DESSUS de la zone du bas (entre l'aperçu et la timeline) :
        # tirer vers le haut agrandit la timeline en rognant l'aperçu, vers le
        # bas la réduit.
        self.tl_sizer = tk.Frame(self.root, height=8, bg="#2a2a2a",
                                 cursor="sb_v_double_arrow")
        self.tl_sizer.pack(fill="x", padx=8, pady=(2, 0))
        tk.Frame(self.tl_sizer, height=2, width=46, bg="#555").place(
            relx=0.5, rely=0.5, anchor="center")  # grip visuel
        self.tl_sizer.bind("<Enter>", lambda _: self.tl_sizer.config(bg="#3a3a3a"))
        self.tl_sizer.bind("<Leave>", lambda _: self.tl_sizer.config(bg="#2a2a2a"))
        self.tl_sizer.bind("<Button-1>", self._sizer_down)
        self.tl_sizer.bind("<B1-Motion>", self._sizer_drag)

        # ── Zone du bas : en-tête commun + corps basculable ────────────────
        # L'en-tête porte un toggle (deux boutons thémés, l'actif désactivé) qui
        # bascule le corps entre l'éditeur timeline et les contrôles de
        # génération. Le toggle reste visible dans les deux modes.
        self.tl_wrap = tk.Frame(self.root, bg="#111")
        self.tl_wrap.pack(fill="x", padx=8, pady=(0, 4))

        tl_hdr = tk.Frame(self.tl_wrap, bg="#111")
        tl_hdr.pack(fill="x", padx=4, pady=(3, 2))

        # Toggle « onglet » : mêmes boutons que le reste de la barre, le mode
        # actif étant signalé par le bouton désactivé (donc non cliquable).
        self._mode_btns: dict[str, ttk.Button] = {}
        for _mode, _txt in (("timeline", "Timeline"),
                            ("generer", "🎬  Générer la vidéo"),
                            ("journal", "📋  Journal")):
            b = ttk.Button(tl_hdr, text=_txt,
                           command=lambda m=_mode: self._set_mode(m))
            b.pack(side="left", padx=(0, 3))
            self._mode_btns[_mode] = b

        ttk.Separator(tl_hdr, orient="vertical").pack(
            side="left", fill="y", padx=8, pady=2)

        # Actions d'édition de la timeline (visibles en mode « timeline »).
        self.tl_actions = tk.Frame(tl_hdr, bg="#111")
        self.tl_actions.pack(side="left", fill="x")
        for _txt, _cmd in (("+ Narration", self._add_narration),
                           ("+ Capture", self._add_capture),
                           ("+ Flèche", self._add_arrow),
                           ("+ Highlight", self._add_highlight),
                           ("+ Texte", self._add_texte),
                           ("+ Zoom", self._add_zoom),
                           ("+ Sample", self._add_sample)):
            ttk.Button(self.tl_actions, text=_txt, command=_cmd).pack(
                side="left", padx=3)
        self.btn_del = ttk.Button(self.tl_actions, text="🗑  Supprimer le bloc",
                                   command=self._del_selected, state="disabled")
        self.btn_del.pack(side="left", padx=(14, 3))

        # Corps basculable : timeline ET génération vivent dans un conteneur
        # de hauteur FIXE (pack_propagate désactivé) pour que ni la bascule ni
        # l'ajout de pistes ne changent le layout — la timeline défile.
        self.tl_body = tk.Frame(self.tl_wrap, bg="#111", height=TL_MIN_BODY)
        self.tl_body.pack(fill="x", padx=4, pady=(0, 4))
        self.tl_body.pack_propagate(False)

        # Corps « timeline » : la piste, défilable verticalement (l'ajout de
        # pistes alimente la zone de scroll sans agrandir le corps).
        self.tl_canvas = tk.Canvas(self.tl_body, bg="#1a1a1a",
                                    highlightthickness=0)
        self.tl_vsb = ttk.Scrollbar(self.tl_body, orient="vertical",
                                    command=self.tl_canvas.yview)
        self.tl_canvas.configure(yscrollcommand=self.tl_vsb.set)
        self.tl_canvas.bind("<Button-1>", self._tl_down)
        self.tl_canvas.bind("<B1-Motion>", self._tl_move)
        self.tl_canvas.bind("<ButtonRelease-1>", self._tl_up)
        # Ajustement fin du début du bloc sélectionné (synchro entre pistes) :
        # ←/→ pas de 0.05 s, Maj+←/→ pas fin de 0.01 s.
        self.tl_canvas.bind("<Left>", lambda e: self._tl_nudge(-0.05))
        self.tl_canvas.bind("<Right>", lambda e: self._tl_nudge(+0.05))
        self.tl_canvas.bind("<Shift-Left>", lambda e: self._tl_nudge(-0.01))
        self.tl_canvas.bind("<Shift-Right>", lambda e: self._tl_nudge(+0.01))
        self._scroll_canvases.append(self.tl_canvas)
        def _tl_resize(e):
            if self._tl_drawing:
                return
            if e.width > 1 and e.width != self._tl_cw:
                self._tl_cw = e.width
                self.root.after_idle(self._draw_timeline)
        self.tl_canvas.bind("<Configure>", _tl_resize)

        # Corps « génération » : bouton + progression + journal.
        self.gen_wrap = tk.Frame(self.tl_body, bg="#111")
        bas = ttk.Frame(self.gen_wrap)
        self.bas = bas
        bas.pack(fill="x", pady=(2, 4))
        self.btn_gen = ttk.Button(bas, text="🎬  Générer la vidéo",
                                   command=self._generer)
        self.btn_gen.pack(side="left")
        self.btn_open = ttk.Button(bas, text="Ouvrir la vidéo",
                                    command=self._ouvrir_video, state="disabled")
        self.btn_open.pack(side="left", padx=6)
        self.prog = ttk.Progressbar(bas, mode="indeterminate")
        self.prog.pack(side="left", fill="x", expand=True, padx=8)

        self.log = tk.Text(self.gen_wrap, wrap="word", state="disabled",
                           font=("Menlo", 10), bg="#0d0d0d", fg="#ccc")
        self.log.pack(fill="both", expand=True, pady=(0, 2))

        # Corps « journal » : journal persistant, toujours accessible via
        # l'onglet « 📋 Journal ». Mêmes messages que le journal de génération,
        # mais cumulés sur toute la session et affichés du plus récent (haut)
        # au plus ancien, avec défilement vertical.
        self.jour_wrap = tk.Frame(self.tl_body, bg="#111")
        jvsb = ttk.Scrollbar(self.jour_wrap, orient="vertical")
        jvsb.pack(side="right", fill="y")
        self.journal = tk.Text(self.jour_wrap, wrap="word", state="disabled",
                               font=("Menlo", 10), bg="#0d0d0d", fg="#ccc",
                               yscrollcommand=jvsb.set)
        self.journal.pack(side="left", fill="both", expand=True, pady=(0, 2))
        jvsb.config(command=self.journal.yview)

        # Molette/trackpad : routeur global (après création de tous les widgets).
        self._init_molette()

        # Mode initial : timeline visible, génération masquée.
        self._set_mode("timeline")

    # ── Molette / trackpad ──────────────────────────────────────────────────
    def _init_molette(self):
        """Défilement molette + trackpad, routé globalement.

        Sur macOS, les widgets enfants (Entry, Label…) « captent » l'évènement :
        un binding sur le canvas ne suffit pas → on écoute globalement
        (`bind_all`). On route vers le canvas défilant le plus proche sous le
        curseur, avec repli sur le dernier canvas survolé.

        Évènements pris en charge :
          - `<MouseWheel>` (vraie molette macOS/Windows) ;
          - `<Button-4/5>` (molette X11/Linux) ;
          - `<TouchpadScroll>` (Tk 9+) : le trackpad macOS/Windows n'émet PLUS
            `<MouseWheel>` mais cet évènement dédié — d'où le trackpad « mort »
            sans ça."""
        self._wheel_target = None
        for cnv in self._scroll_canvases:
            cnv.bind("<Enter>",
                     lambda e, c=cnv: setattr(self, "_wheel_target", c), add="+")
        for w in (self.apercu_canvas, self.log, self.journal):
            w.bind("<Enter>",
                   lambda e: setattr(self, "_wheel_target", None), add="+")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.root.bind_all(seq, self._wheel_router, add="+")
        try:  # Tk 9+ uniquement ; no-op (TclError) sur Tk 8.x
            self.root.bind_all("<TouchpadScroll>", self._touchpad_scroll, add="+")
        except tk.TclError:
            pass

    def _cible_molette(self, event):
        """Canvas défilant visé : le plus proche sous le curseur, sinon le
        dernier survolé (repli quand la résolution échoue, fréquent au trackpad)."""
        node = self.root.winfo_containing(event.x_root, event.y_root)
        while node is not None:
            if node in self._scroll_canvases:
                return node
            node = getattr(node, "master", None)
        return getattr(self, "_wheel_target", None)

    def _wheel_router(self, event):
        cible = self._cible_molette(event)
        if cible is None:
            return None  # laisse le défilement natif (ex. journal Text)
        num = getattr(event, "num", 0)
        if num == 4:        # X11 molette haut
            pas = -1
        elif num == 5:      # X11 molette bas
            pas = 1
        else:
            d = event.delta or 0
            if d == 0:
                return "break"
            # 1 cran mini ; Windows : multiples de 120.
            mag = max(1, abs(d) // 120) if abs(d) >= 120 else 1
            pas = -mag if d > 0 else mag
        try:
            cible.yview_scroll(pas, "units")
        except Exception:
            pass
        return "break"

    def _touchpad_scroll(self, event):
        """Trackpad (Tk 9+). `event.delta` empaquette deltaX (16 bits hauts) et
        deltaY (16 bits bas, signé). On throttle (~1 évènement sur 3) car le
        trackpad en émet beaucoup."""
        cible = self._cible_molette(event)
        if cible is None:
            return None
        if getattr(event, "serial", 0) % 3 != 0:  # throttle
            return "break"
        dy = event.delta & 0xFFFF
        if dy > 0x7FFF:
            dy -= 0x10000
        if dy:
            try:
                cible.yview_scroll(-dy, "units")
            except Exception:
                pass
        return "break"

    # ============================================================ SCENE LIST
    def _nouvel_id(self, prefix: str) -> str:
        ex = {s.id for s in self.scenes}
        i = 1
        while f"{prefix}_{i}" in ex:
            i += 1
        return f"{prefix}_{i}"

    def _lbl_scene(self, s: config.Scene) -> str:
        return f" {'▶' if s.type == 'title' else '⬛'}  {s.titre or s.id}"

    def _refresh_liste(self, sel: int | None = None):
        for w in self.scene_list_frame.winfo_children():
            w.destroy()
        self.scene_rows = []
        for i, s in enumerate(self.scenes):
            row = tk.Frame(self.scene_list_frame, bg="#161616")
            row.pack(fill="x", padx=2, pady=1)
            lbl = tk.Label(row, text=self._lbl_scene(s), bg="#161616", fg="#ddd",
                           anchor="w", font=("Helvetica", 11), cursor="hand2")
            lbl.pack(side="left", fill="x", expand=True)
            # La première slide est obligatoire : pas de croix de suppression.
            if i > 0:
                croix = tk.Label(row, text="✕", bg="#161616", fg="#9a5a5a",
                                 font=("Helvetica", 11, "bold"), cursor="hand2")
                croix.pack(side="right", padx=(2, 4))
                croix.bind("<Button-1>", lambda e, idx=i: self._supprimer_idx(idx))
                croix.bind("<Enter>", lambda e, c=croix: c.config(fg="#ff6b6b"))
                croix.bind("<Leave>", lambda e, c=croix: c.config(fg="#9a5a5a"))
            for w in (row, lbl):
                w.bind("<Button-1>", lambda e, idx=i: self._select_scene(idx))
            self.scene_rows.append({"row": row, "lbl": lbl})
        if sel is not None and 0 <= sel < len(self.scenes):
            self.current = sel
        self._maj_surbrillance()

    def _maj_surbrillance(self):
        for i, r in enumerate(self.scene_rows):
            actif = (i == self.current)
            bg = VERT if actif else "#161616"
            fg = "white" if actif else "#ddd"
            r["row"].config(bg=bg)
            r["lbl"].config(bg=bg, fg=fg)

    def _nouvelle_scene(self, type_: str) -> config.Scene:
        if type_ == "title":
            return config.Scene(id=self._nouvel_id("intro"), type="title",
                                titre="Nouveau titre", sous_titre="",
                                duree_min=3.0)
        dp = config.DEF_POS["screenshot"]
        dl = config.DEF_LOGO["screenshot"]
        return config.Scene(
            id=self._nouvel_id("etape"), type="screenshot",
            titre="Nouvelle étape", duree_min=5.0,
            titre_x=dp["titre"][0], titre_y=dp["titre"][1],
            sous_titre_x=dp["sous_titre"][0], sous_titre_y=dp["sous_titre"][1],
            logo_x=dl[0], logo_y=dl[1], logo_echelle=dl[2],
            narrations=[config.Narration("Décrivez ici l'action à l'écran.")],
            captures=[config.Capture(None)],
        )

    def _ajouter(self, type_: str):
        s = self._nouvelle_scene(type_)
        idx = (self.current + 1) if self.current is not None else len(self.scenes)
        self.scenes.insert(idx, s)
        self._refresh_liste(idx)
        self._select_scene(idx)

    def _dupliquer(self):
        if self.current is None:
            return
        import copy
        s = copy.deepcopy(self.scenes[self.current])
        s.id = self._nouvel_id(s.type == "title" and "intro" or "etape")
        s.titre = f"{s.titre} (copie)"
        self.scenes.insert(self.current + 1, s)
        idx = self.current + 1
        self._refresh_liste(idx)
        self._select_scene(idx)

    def _supprimer_idx(self, idx: int):
        # La première slide est obligatoire et ne peut pas être supprimée.
        if not (0 < idx < len(self.scenes)):
            return
        del self.scenes[idx]
        if self.current is None or self.current >= len(self.scenes):
            self.current = len(self.scenes) - 1
        elif idx < self.current:
            self.current -= 1
        self._refresh_liste(self.current)
        self._select_scene(self.current)

    def _deplacer(self, d: int):
        if self.current is None:
            return
        j = self.current + d
        if not 0 <= j < len(self.scenes):
            return
        self.scenes[self.current], self.scenes[j] = self.scenes[j], self.scenes[self.current]
        self._refresh_liste(j)
        self._select_scene(j)

    def _select_scene(self, idx: int):
        if self._playing or self._play_intent:
            self._play_stop()
        if not self.scenes:
            self.current = None
        else:
            self.current = max(0, min(idx, len(self.scenes) - 1))
        self._sel = None
        self._tl_drag = None
        self._preview_t = 0.0
        self.btn_del.config(state="disabled")
        self._maj_surbrillance()
        self._build_settings()
        self._plan_apercu()
        self.root.after_idle(self._draw_timeline)

    # ================================================================ META
    def _maj_meta(self, *_):
        if self._chargement:
            return
        self.meta.titre = self.titre_var.get()
        self.meta.app = self.app_var.get()
        self.meta.voix = self.voix_var.get()
        self.reglages.voix = self.meta.voix
        settings.sauver(self.reglages)
        self._plan_apercu()

    # ── Résolution de sortie ────────────────────────────────────────────────
    def _reglage_resolution(self) -> tuple[int, int]:
        """Résolution globale persistée, normalisée en tuple (défaut 1920×1080)."""
        res = getattr(self.reglages, "resolution", None) or [1920, 1080]
        try:
            return (int(res[0]), int(res[1]))
        except (TypeError, ValueError, IndexError):
            return (1920, 1080)

    def _on_resolution(self, label: str):
        """Applique la résolution choisie : projet courant + réglage global."""
        for lbl, res in settings.RESOLUTIONS:
            if lbl == label:
                self.meta.resolution = res
                self.reglages.resolution = list(res)
                settings.sauver(self.reglages)
                self._plan_apercu()
                return

    # ── Police globale ──────────────────────────────────────────────────────
    def _choisir_police(self):
        f = filedialog.askopenfilename(
            title="Choisir une police",
            initialdir="/System/Library/Fonts",
            filetypes=[("Polices", "*.ttf *.otf *.ttc"), ("Tous", "*.*")])
        if not f:
            return
        self.reglages.police = f
        settings.sauver(self.reglages)
        self.meta.police = Path(self._adopter(f))
        self._maj_lbl_police()
        self._plan_apercu()

    def _effacer_police(self):
        self.meta.police = None
        self.reglages.police = None
        settings.sauver(self.reglages)
        self._maj_lbl_police()
        self._plan_apercu()

    def _maj_lbl_police(self):
        if not (self.lbl_police and self.lbl_police.winfo_exists()):
            return
        if self.meta.police and Path(self.meta.police).is_file():
            self.lbl_police.config(text=f"🅰 {Path(self.meta.police).name}")
        else:
            self.lbl_police.config(text="(police par défaut)")

    # ── Voix de référence (clonage XTTS) ────────────────────────────────────
    def _choisir_voix_reference(self):
        f = filedialog.askopenfilename(
            title="Choisir un échantillon de voix (WAV mono, 5-10 s)",
            filetypes=[("Audio", "*.wav *.flac *.mp3 *.m4a *.aiff"), ("Tous", "*.*")])
        if not f:
            return
        self.reglages.voix_reference = f
        settings.sauver(self.reglages)
        self.meta.voix_reference = Path(self._adopter(f))
        self._maj_lbl_voix_ref()

    def _effacer_voix_reference(self):
        self.meta.voix_reference = None
        self.reglages.voix_reference = None
        settings.sauver(self.reglages)
        self._maj_lbl_voix_ref()

    def _maj_lbl_voix_ref(self):
        if not (self.lbl_voix_ref and self.lbl_voix_ref.winfo_exists()):
            return
        if self.meta.voix_reference and Path(self.meta.voix_reference).is_file():
            self.lbl_voix_ref.config(text=f"🎙 {Path(self.meta.voix_reference).name}")
        else:
            self.lbl_voix_ref.config(text="(voix par défaut)")

    # ── Fond : couleur / dégradé / image ────────────────────────────────────
    def _build_fond_controls(self, parent):
        """(Re)construit les contrôles de fond selon le type choisi."""
        self._fond_box = parent
        for w in parent.winfo_children():
            w.destroy()

        trow = ttk.Frame(parent)
        trow.pack(fill="x")
        type_var = tk.StringVar(value=getattr(self.meta, "fond_type", "couleur"))
        for val, lbl in (("couleur", "Couleur"), ("degrade", "Dégradé"),
                         ("image", "Image")):
            ttk.Radiobutton(trow, text=lbl, value=val, variable=type_var,
                            command=lambda v=val: self._set_fond_type(v)).pack(
                side="left", padx=(0, 8))

        t = getattr(self.meta, "fond_type", "couleur")
        if t in ("couleur", "degrade"):
            crow = ttk.Frame(parent)
            crow.pack(fill="x", pady=(6, 0))
            ttk.Label(crow, text="Couleur 1").pack(side="left")
            # tk.Button ignore `bg` sous macOS (rendu natif Aqua → carré blanc) :
            # on utilise un Label, qui respecte la couleur de fond.
            self.btn_fond = tk.Label(crow, bg=self.meta.couleur_fond, width=4,
                                     relief="groove", borderwidth=2, cursor="hand2")
            self.btn_fond.bind("<Button-1>", lambda *_: self._choisir_fond())
            self.btn_fond.pack(side="left", padx=4)
            if t == "degrade":
                ttk.Label(crow, text="Couleur 2").pack(side="left", padx=(10, 0))
                self.btn_fond2 = tk.Label(crow, bg=self.meta.couleur_fond2,
                                          width=4, relief="groove", borderwidth=2,
                                          cursor="hand2")
                self.btn_fond2.bind("<Button-1>", lambda *_: self._choisir_fond2())
                self.btn_fond2.pack(side="left", padx=4)
                srow = ttk.Frame(parent)
                srow.pack(fill="x", pady=(6, 0))
                ttk.Label(srow, text="Sens :").pack(side="left")
                sens_var = tk.StringVar(value=self.meta.degrade_sens)
                ttk.Combobox(srow, textvariable=sens_var, state="readonly",
                             width=12, values=["vertical", "horizontal", "diagonal"]
                             ).pack(side="left", padx=4)
                sens_var.trace_add(
                    "write", lambda *_: self._set_degrade_sens(sens_var.get()))
        else:  # image
            irow = ttk.Frame(parent)
            irow.pack(fill="x", pady=(6, 0))
            ttk.Button(irow, text="Choisir une image…",
                       command=self._choisir_image_fond).pack(side="left")
            ttk.Button(irow, text="✕", width=2,
                       command=self._effacer_image_fond).pack(side="left", padx=(4, 0))
            self.lbl_fond_img = ttk.Label(parent, text="", foreground="#888",
                                          wraplength=300)
            self.lbl_fond_img.pack(fill="x", pady=(2, 0))
            self._maj_lbl_fond_img()

    def _set_fond_type(self, val: str):
        self.meta.fond_type = val
        self.reglages.fond_type = val
        settings.sauver(self.reglages)
        if self._fond_box and self._fond_box.winfo_exists():
            self._build_fond_controls(self._fond_box)
        self._plan_apercu()

    def _set_degrade_sens(self, sens: str):
        self.meta.degrade_sens = sens
        self.reglages.degrade_sens = sens
        settings.sauver(self.reglages)
        self._plan_apercu()

    def _choisir_fond2(self):
        c = colorchooser.askcolor(color=self.meta.couleur_fond2,
                                  title="2e couleur du dégradé")
        if c and c[1]:
            self.meta.couleur_fond2 = c[1]
            self.reglages.couleur_fond2 = c[1]
            settings.sauver(self.reglages)
            if self.btn_fond2 and self.btn_fond2.winfo_exists():
                self.btn_fond2.config(bg=c[1])
            self._plan_apercu()

    def _choisir_image_fond(self):
        f = filedialog.askopenfilename(
            title="Image de fond",
            filetypes=imaging.motif_filetypes())
        if not f:
            return
        self.reglages.fond_image = f
        settings.sauver(self.reglages)
        self.meta.fond_image = Path(self._adopter(f))
        self._maj_lbl_fond_img()
        self._plan_apercu()

    def _effacer_image_fond(self):
        self.meta.fond_image = None
        self.reglages.fond_image = None
        settings.sauver(self.reglages)
        self._maj_lbl_fond_img()
        self._plan_apercu()

    def _maj_lbl_fond_img(self):
        if not (self.lbl_fond_img and self.lbl_fond_img.winfo_exists()):
            return
        if self.meta.fond_image and Path(self.meta.fond_image).is_file():
            self.lbl_fond_img.config(text=f"🖼 {Path(self.meta.fond_image).name}")
        else:
            self.lbl_fond_img.config(text="(aucune image)")

    def _choisir_fond(self):
        c = colorchooser.askcolor(color=self.meta.couleur_fond, title="Couleur de fond")
        if c and c[1]:
            self.meta.couleur_fond = c[1]
            self.reglages.couleur_fond = c[1]
            settings.sauver(self.reglages)
            if self.btn_fond and self.btn_fond.winfo_exists():
                self.btn_fond.config(bg=c[1])
            self._plan_apercu()

    def _choisir_sous_titre_fond(self):
        c = colorchooser.askcolor(color=self.meta.sous_titre_fond,
                                  title="Couleur de fond des sous-titres")
        if c and c[1]:
            self.meta.sous_titre_fond = c[1]
            self.reglages.sous_titre_fond = c[1]
            settings.sauver(self.reglages)
            if self.btn_st_fond and self.btn_st_fond.winfo_exists():
                self.btn_st_fond.config(bg=c[1])
            self._plan_apercu()

    def _effacer_logo(self):
        self.meta.logo = None
        self.reglages.logo = None
        settings.sauver(self.reglages)
        self._maj_lbl_logo()
        self._plan_apercu()

    def _choisir_logo(self):
        f = filedialog.askopenfilename(
            title="Choisir un logo (PNG conseillé, transparence supportée)",
            filetypes=imaging.motif_filetypes())
        if not f:
            return
        self.reglages.logo = f
        settings.sauver(self.reglages)
        self.meta.logo = Path(self._adopter(f))
        self._maj_lbl_logo()
        self._plan_apercu()

    def _maj_lbl_logo(self):
        if not (self.lbl_logo and self.lbl_logo.winfo_exists()):
            return
        if self.meta.logo and Path(self.meta.logo).is_file():
            self.lbl_logo.config(text=f"🖼 {Path(self.meta.logo).name}")
        else:
            self.lbl_logo.config(text="(logo automatique)")



def _appliquer_theme(root: tk.Tk):
    """Force un thème sombre cohérent, indépendant du mode clair/sombre du
    système (l'UI est conçue pour le dark : on l'impose partout)."""
    BG = "#1e1e1e"      # fond général
    FG = "#e6e6e6"      # texte
    FIELD = "#2b2b2b"   # champs (entry/combobox)
    BTN = "#323232"     # boutons
    BORD = "#3a3a3a"

    # Widgets « classiques » (tk.*) : valeurs par défaut globales.
    try:
        root.tk_setPalette(
            background=BG, foreground=FG,
            activeBackground="#3a3a3a", activeForeground=FG,
            selectBackground=VERT, selectForeground="white",
            highlightBackground=BG, highlightColor=VERT,
            insertBackground=FG, troughColor=FIELD,
            disabledForeground="#777")
    except Exception:
        pass

    st = ttk.Style()
    try:
        st.theme_use("clam")        # seul thème entièrement recolorable
    except Exception:
        pass
    st.configure(".", background=BG, foreground=FG, fieldbackground=FIELD,
                 bordercolor=BORD, lightcolor=BG, darkcolor=BG,
                 troughcolor=FIELD, focuscolor=VERT, insertcolor=FG)
    st.configure("TFrame", background=BG)
    st.configure("TLabelframe", background=BG, bordercolor=BORD)
    st.configure("TLabelframe.Label", background=BG, foreground=FG)
    st.configure("TLabel", background=BG, foreground=FG)
    st.configure("TButton", background=BTN, foreground=FG, bordercolor=BORD,
                 focusthickness=0, padding=5)
    st.map("TButton",
           background=[("active", "#3d3d3d"), ("pressed", "#474747"),
                       ("disabled", "#262626")],
           foreground=[("disabled", "#777")])
    st.configure("TEntry", fieldbackground=FIELD, foreground=FG,
                 insertcolor=FG, bordercolor=BORD)
    st.configure("TCombobox", fieldbackground=FIELD, foreground=FG,
                 background=BTN, arrowcolor=FG, bordercolor=BORD)
    st.map("TCombobox", fieldbackground=[("readonly", FIELD)],
           foreground=[("readonly", FG)], arrowcolor=[("disabled", "#666")])
    for cls in ("TCheckbutton", "TRadiobutton"):
        st.configure(cls, background=BG, foreground=FG, focuscolor=BG)
        st.map(cls, background=[("active", BG)], foreground=[("disabled", "#777")])
    st.configure("Horizontal.TScale", background=BG, troughcolor=FIELD)
    st.configure("TScrollbar", background=BTN, troughcolor=BG,
                 arrowcolor=FG, bordercolor=BG)
    st.map("TScrollbar", background=[("active", "#444")])
    st.configure("Horizontal.TProgressbar", background=VERT,
                 troughcolor=FIELD, bordercolor=BG)
    st.configure("TSeparator", background=BORD)

    # Liste déroulante des Combobox (popup = Listbox classique).
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", VERT)
    root.option_add("*TCombobox*Listbox.selectForeground", "white")
    root.configure(bg=BG)


def main() -> int:
    settings.assurer_dossiers()
    root = tk.Tk()
    _appliquer_theme(root)
    Editor(root)
    root.update_idletasks()
    root.deiconify()
    root.lift()
    root.attributes("-topmost", True)
    root.after(500, lambda: root.attributes("-topmost", False))
    root.mainloop()
    return 0
