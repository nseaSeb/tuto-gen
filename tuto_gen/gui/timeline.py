"""Timeline : pistes, rendu, drag/resize + ajout de blocs."""

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


class TimelineMixin:
    # =========================================================== TIMELINE
    def _tl_duree(self) -> float:
        if self.current is None:
            return 5.0
        s = self.scenes[self.current]
        return max(s.duree_min if s.duree_min and s.duree_min > 0 else 5.0, 0.5)

    def _tl_tick_step(self, dur: float) -> float:
        if dur <= 10:   return 1.0
        if dur <= 30:   return 2.0
        if dur <= 60:   return 5.0
        if dur <= 120:  return 10.0
        return 30.0

    def _tl_tracks(self) -> list[dict]:
        if self.current is None:
            return []
        s = self.scenes[self.current]
        tracks = [{"kind": "scene_dur", "idx": 0, "label": "Durée scène",
                   "color": COUL["scene_dur"], "resizable": "end"}]
        for i, n in enumerate(s.narrations):
            # La durée d'une narration est TOUJOURS celle de l'audio : bloc
            # déplaçable, jamais redimensionnable.
            tracks.append({"kind": "narration", "idx": i,
                           "label": f"Narration {i + 1}",
                           "color": COUL["narration"], "resizable": "move"})
        for i, _c in enumerate(s.captures):
            tracks.append({"kind": "capture", "idx": i,
                           "label": f"Capture {i + 1}",
                           "color": COUL["capture"], "resizable": "both"})
        arrow_n = hl_n = 0
        for i, a in enumerate(s.annotations):
            if a.type == "arrow":
                arrow_n += 1
                tracks.append({"kind": "arrow", "idx": i,
                               "label": f"Flèche {arrow_n}",
                               "color": a.couleur or COUL["arrow"],
                               "resizable": "both"})
            else:
                hl_n += 1
                tracks.append({"kind": "highlight", "idx": i,
                               "label": f"Highlight {hl_n}",
                               "color": a.couleur or COUL["highlight"],
                               "resizable": "both"})
        for i, tx in enumerate(s.textes):
            apercu = (tx.texte.strip().split("\n")[0] or "Texte")[:14]
            tracks.append({"kind": "texte", "idx": i,
                           "label": f"Texte : {apercu}",
                           "color": COUL["texte"], "resizable": "both"})
        for j, sa in enumerate(s.samples):
            nm = Path(sa.chemin).name if sa.chemin else f"Sample {j+1}"
            if len(nm) > 14:
                nm = nm[:12] + "…"
            # Fichier présent + « fin » libre → durée verrouillée sur celle du
            # fichier audio : bloc déplaçable mais non redimensionnable.
            verrou = (sa.fin is None and sa.chemin and Path(sa.chemin).is_file())
            tracks.append({"kind": "sample", "idx": j, "label": nm,
                           "color": COUL["sample"],
                           "resizable": "move" if verrou else "both"})
        return tracks

    def _tl_x(self, t: float, cw: int) -> int:
        avail = max(cw - TL_LEFT - 8, 1)
        return TL_LEFT + int(t / self._tl_duree() * avail)

    def _tl_t(self, x: int, cw: int) -> float:
        avail = max(cw - TL_LEFT - 8, 1)
        return max(0.0, min(self._tl_duree(),
                            (x - TL_LEFT) / avail * self._tl_duree()))

    def _tl_row_y(self, row: int) -> int:
        return TL_TOP + row * (TL_ROW_H + TL_GAP)

    def _sample_dur(self, chemin) -> float:
        k = str(chemin)
        if k not in self._sample_durees:
            try:
                import soundfile as sf
                self._sample_durees[k] = sf.info(k).duration
            except Exception:
                self._sample_durees[k] = 2.0
        return self._sample_durees[k]

    def _narr_dur(self, n) -> float | None:
        """Durée de l'audio en cache pour la narration `n`, ou None si non généré.

        Mémoïsée par (texte + paramètres de voix). On ne mémoïse QUE les durées
        réelles : tant que l'audio n'est pas généré on re-vérifie le cache à
        chaque appel (lecture peu coûteuse — `duree_cache` renvoie None sans lire
        le fichier si absent). Ainsi, dès que l'audio est produit par n'importe
        quel chemin (Écouter, Lire, export, génération…), la piste se recale au
        redraw suivant, sans invalidation explicite."""
        from .. import tts
        from .. import config
        texte = (n.texte or "").strip()
        if not texte:
            return None
        p = config.params_voix(self.meta, n)
        key = (texte, str(p["ref_voix"]), p["speaker"], p["speed"],
               p["temperature"], p["fluidite"])
        if key not in self._narr_durees:
            dur = tts.duree_cache(texte, **p)
            if dur is None:
                return None  # pas encore généré : ne pas mémoïser
            self._narr_durees[key] = dur
        return self._narr_durees[key]

    def _narr_durees_info(self, n) -> tuple[float | None, float]:
        """(durée_audio, durée_piste) pour une narration.

        `durée_audio` = durée du mp3/wav généré (None si pas encore généré).
        `durée_piste` = longueur réellement occupée dans la timeline, selon la
        même logique que `_tl_range` (fin explicite, sinon durée audio, sinon
        fin de scène)."""
        audio = self._narr_dur(n)
        d = self._tl_duree()
        end = min(n.debut + audio, d) if audio is not None else d
        return audio, max(0.0, end - n.debut)

    def _span_verrou(self, kind: str, obj) -> float | None:
        """Durée verrouillée d'un bloc « audio » (narration/sample) si sa « fin »
        est libre, sinon None (bloc à durée libre, redimensionnable)."""
        if kind == "narration":
            return self._narr_dur(obj)
        if kind == "sample" and obj.fin is None and getattr(obj, "chemin", None):
            if Path(obj.chemin).is_file():
                return self._sample_dur(obj.chemin)
        return None

    def _maj_scene_duree_var(self, val: float):
        """Reflète une nouvelle durée de scène dans le champ du panneau."""
        if self._scene_duree_var is not None:
            self._chargement = True
            try:
                self._scene_duree_var.set(str(val))
            finally:
                self._chargement = False

    def _poser_debut(self, kind: str, obj, debut: float) -> float:
        """Fixe `obj.debut` en respectant le verrou de durée audio.

        Pour un bloc verrouillé (narration/sample, « fin » libre) : la durée
        reste celle de l'audio ; le début est borné à [0, slide − audio] et, si
        l'audio ne tient pas dans la slide, la slide s'allonge en conséquence.
        Renvoie le début effectif."""
        cur_d = self._tl_duree()
        span = self._span_verrou(kind, obj)
        nd = max(0.0, debut)
        if span is None:
            obj.debut = round(nd, 2)
            return obj.debut
        if nd + span > cur_d:
            nd = max(0.0, round(cur_d - span, 2))
        obj.debut = round(nd, 2)
        obj.fin = None
        besoin = round(obj.debut + span, 2)
        if besoin > cur_d + 1e-6:  # audio plus long que la slide → on allonge
            self.scenes[self.current].duree_min = besoin
            self._maj_scene_duree_var(besoin)
        return obj.debut

    def _normaliser_narration(self, n):
        """Garantit que la durée de la piste narration = durée de l'audio :
        supprime toute « fin » explicite et borne le début (la slide s'allonge si
        l'audio n'y tient pas). Sans effet tant que l'audio n'est pas généré.

        Appelé à la sélection de la narration et quand l'audio est (re)généré."""
        n.fin = None
        self._poser_debut("narration", n, n.debut)

    def _tl_range(self, track: dict) -> tuple[float, float]:
        s = self.scenes[self.current]
        d = self._tl_duree()
        k, i = track["kind"], track["idx"]
        if k == "scene_dur":
            return (0.0, d)
        if k == "narration":
            n = s.narrations[i]
            # Durée toujours imposée par l'audio (la « fin » est ignorée). Si
            # l'audio n'est pas encore généré, on étend jusqu'à la fin de scène.
            dur = self._narr_dur(n)
            if dur is not None:
                return (n.debut, min(n.debut + dur, d))
            return (n.debut, d)
        if k == "capture":
            cap = s.captures[i]
            return (cap.debut, cap.fin if cap.fin is not None else d)
        if k in ("arrow", "highlight"):
            a = s.annotations[i]
            return (a.debut, a.fin if a.fin is not None else d)
        if k == "texte":
            tx = s.textes[i]
            return (tx.debut, tx.fin if tx.fin is not None else d)
        if k == "sample":
            sa = s.samples[i]
            file_end = sa.debut + self._sample_dur(sa.chemin)
            end = sa.fin if sa.fin is not None else file_end
            return (sa.debut, min(end, d))
        return (0.0, d)

    def _draw_timeline(self):
        if self._tl_drawing:
            return
        self._tl_drawing = True
        c = self.tl_canvas
        try:
            cw = c.winfo_width()
            if cw <= 1:
                self.root.after(60, self._draw_timeline)
                return

            tracks = self._tl_tracks() if self.current is not None else []
            n = len(tracks)
            # Hauteur du contenu : alimente la zone de scroll. Le corps lui-même
            # garde une hauteur fixe ; on remplit au moins la partie visible.
            vis = c.winfo_height()
            total_h = max(TL_TOP + n * (TL_ROW_H + TL_GAP) + 8,
                          vis if vis > 1 else TL_MIN_BODY)
            c.config(scrollregion=(0, 0, cw, total_h))
            c.delete("all")

            if self.current is None:
                c.create_text(cw // 2, 40, text="← Sélectionne une scène",
                              fill="#444", font=("Helvetica", 11))
                return

            d = self._tl_duree()

            c.create_rectangle(0, 0, TL_LEFT - 1, total_h, fill="#161616", outline="")
            c.create_rectangle(TL_LEFT, 0, cw, total_h, fill="#1e1e1e", outline="")

            # Règle graduée
            step = self._tl_tick_step(d)
            t = 0.0
            while t <= d + 0.001:
                x = self._tl_x(t, cw)
                c.create_line(x, 0, x, TL_TOP - 4, fill="#555")
                c.create_text(x, 2, text=f"{t:.0f}s",
                              fill="#666", anchor="n", font=("Menlo", 9))
                c.create_line(x, TL_TOP, x, total_h, fill="#252525", dash=(2, 10))
                t = round(t + step, 3)
            c.create_line(0, TL_TOP, cw, TL_TOP, fill="#333")

            # Curseur de lecture (preview_t) — taggué pour pouvoir le déplacer
            # sans redraw complet (cf. _maj_curseur_tl, utilisé en lecture).
            xt = self._tl_x(self._preview_t, cw)
            c.create_line(xt, TL_TOP - 4, xt, total_h, fill="#e0556b", width=1,
                          tags="playhead")

            # Pistes
            for row, tr in enumerate(tracks):
                y = self._tl_row_y(row)
                k = tr["kind"]
                is_sel = (self._sel is not None and
                          self._sel["kind"] == k and self._sel["idx"] == tr["idx"])

                c.create_rectangle(0, y, TL_LEFT - 1, y + TL_ROW_H,
                                   fill="#191919", outline="")
                row_bg = "#222" if row % 2 == 0 else "#1d1d1d"
                c.create_rectangle(TL_LEFT, y, cw, y + TL_ROW_H,
                                   fill=row_bg, outline="")
                c.create_line(0, y + TL_ROW_H, cw, y + TL_ROW_H, fill="#2a2a2a")

                lc = "#fff" if is_sel else "#888"
                lf = ("Helvetica", 9, "bold") if is_sel else ("Helvetica", 9)
                c.create_text(TL_LEFT - 6, y + TL_ROW_H // 2,
                              text=tr["label"], fill=lc, anchor="e", font=lf)

                debut, fin = self._tl_range(tr)
                x1 = self._tl_x(debut, cw)
                x2 = max(x1 + TL_HANDLE * 2 + 6, self._tl_x(fin, cw))
                pad = 4
                bcolor = tr["color"]

                c.create_rectangle(x1, y + pad, x2, y + TL_ROW_H - pad,
                                   fill=bcolor, outline=_lighten(bcolor, 0.3))
                c.create_rectangle(x1, y + pad, x2,
                                   y + pad + (TL_ROW_H - 2 * pad) // 2,
                                   fill=_lighten(bcolor, 0.18), outline="")
                if is_sel:
                    c.create_rectangle(x1 - 1, y + pad - 1, x2 + 1,
                                       y + TL_ROW_H - pad + 1,
                                       outline="#ffffff", width=2, fill="")

                bw = x2 - x1
                if bw > 44:
                    c.create_text(x1 + bw // 2, y + TL_ROW_H // 2,
                                  text=tr["label"], fill="#f5f5f5",
                                  font=("Helvetica", 8), anchor="center")

                # Durée de la piste, pour caler le timing d'un coup d'œil.
                seg = fin - debut
                if seg > 0.001:
                    dtxt = f"{seg:.1f}s"
                    if x2 + 6 < cw - 4:  # place à droite du bloc si possible
                        c.create_text(x2 + 6, y + TL_ROW_H // 2, text=dtxt,
                                      fill="#9a9a9a", anchor="w",
                                      font=("Menlo", 8))
                    elif bw > 30:        # sinon à l'intérieur, bord droit
                        c.create_text(x2 - 4, y + TL_ROW_H // 2, text=dtxt,
                                      fill="#101010", anchor="e",
                                      font=("Menlo", 8))

                py1, py2 = y + pad + 2, y + TL_ROW_H - pad - 2
                res = tr["resizable"]
                if res in ("both", "start"):
                    c.create_rectangle(x1, py1, x1 + TL_HANDLE, py2,
                                       fill="#f0f0f0", outline="")
                if res in ("both", "end"):
                    c.create_rectangle(x2 - TL_HANDLE, py1, x2, py2,
                                       fill="#f0f0f0", outline="")
        finally:
            self._tl_drawing = False

    def _maj_curseur_tl(self):
        """Déplace le seul curseur de lecture (sans reconstruire la timeline).

        Utilisé en lecture/scrub quand seul `preview_t` change. Repli sur un
        redraw complet si le curseur n'existe pas encore (timeline non dessinée
        ou pistes à reconstruire)."""
        c = self.tl_canvas
        items = c.find_withtag("playhead")
        if not items:
            self._draw_timeline()
            return
        cw = c.winfo_width()
        xt = self._tl_x(self._preview_t, cw)
        try:
            total_h = float(str(c.cget("scrollregion")).split()[3])
        except (IndexError, ValueError, TypeError):
            total_h = c.winfo_height()
        for it in items:
            c.coords(it, xt, TL_TOP - 4, xt, total_h)

    def _tl_hit(self, x: int, y: int, cw: int) -> dict | None:
        if self.current is None:
            return None
        for row, tr in enumerate(self._tl_tracks()):
            ty = self._tl_row_y(row)
            if not (ty <= y <= ty + TL_ROW_H):
                continue
            debut, fin = self._tl_range(tr)
            x1 = self._tl_x(debut, cw)
            x2 = max(x1 + TL_HANDLE * 2 + 6, self._tl_x(fin, cw))
            if not (x1 <= x <= x2):
                return {**tr, "part": "select"}  # clic sur la piste (scrub)
            res = tr["resizable"]
            if res is None:
                return {**tr, "part": "select"}
            if res == "move":  # déplaçable seulement (durée verrouillée)
                return {**tr, "part": "body"}
            if res == "end":
                return {**tr, "part": "end" if x2 - x <= TL_HANDLE else "select"}
            if x - x1 <= TL_HANDLE:
                return {**tr, "part": "start"}
            if x2 - x <= TL_HANDLE:
                return {**tr, "part": "end"}
            return {**tr, "part": "body"}
        return None

    def _tl_down(self, ev):
        if self._playing or self._play_intent:
            self._play_stop()
        self.tl_canvas.focus_set()  # pour recevoir les flèches (nudge)
        if self.current is None:
            return
        cw = self.tl_canvas.winfo_width()
        # Scrub : positionne l'aperçu au temps cliqué
        self._preview_t = round(self._tl_t(ev.x, cw), 2)
        cy = int(self.tl_canvas.canvasy(ev.y))  # tient compte du scroll vertical
        hit = self._tl_hit(ev.x, cy, cw)
        if hit is None:
            self._plan_apercu()
            self._draw_timeline()
            return
        k, i, part = hit["kind"], hit["idx"], hit["part"]

        self._sel = {"kind": k, "idx": i}
        can_del = k in ("narration", "capture", "arrow", "highlight", "sample")
        self.btn_del.config(state="normal" if can_del else "disabled")
        self._build_settings()
        self._plan_apercu()
        self._draw_timeline()

        if part == "select":
            self._tl_drag = None
            return

        s = self.scenes[self.current]
        d = self._tl_duree()
        debut, fin = self._tl_range(hit)
        self._tl_drag = {"kind": k, "idx": i, "part": part, "x0": ev.x,
                         "orig_d": debut, "orig_f": fin, "ref_dur": d}
        self._draw_timeline()

    def _tl_move(self, ev):
        if not self._tl_drag or self.current is None:
            return
        dr = self._tl_drag
        cw = self.tl_canvas.winfo_width()
        avail = max(cw - TL_LEFT - 8, 1)
        dt = (ev.x - dr["x0"]) / avail * dr["ref_dur"]
        s = self.scenes[self.current]
        k, i, part = dr["kind"], dr["idx"], dr["part"]
        od, of_ = dr["orig_d"], dr["orig_f"]
        cur_d = self._tl_duree()

        if k == "scene_dur":
            new_d = max(0.5, round(of_ + dt, 1))
            s.duree_min = new_d
            if self._scene_duree_var is not None:
                self._chargement = True
                try:
                    self._scene_duree_var.set(str(new_d))
                finally:
                    self._chargement = False
            self._draw_timeline()
            return

        obj = self._tl_obj(k, i)
        if obj is None:
            return

        # Narration : durée TOUJOURS = audio, jamais de fin explicite. On déplace
        # seulement (début borné, slide allongée si besoin via _poser_debut).
        if k == "narration":
            self._poser_debut("narration", obj, od + dt)
            obj.fin = None
            self._preview_t = obj.debut
            self._sync_timing_vars(obj.debut, obj.fin)
            self._draw_timeline()
            return

        # Sample verrouillé (fin libre) : durée imposée par le fichier audio.
        if self._span_verrou(k, obj) is not None:
            self._poser_debut(k, obj, od + dt)
            self._preview_t = obj.debut
            self._sync_timing_vars(obj.debut, obj.fin)
            self._draw_timeline()
            return

        if part == "start":
            obj.debut = round(max(0.0, min(of_ - 0.05, od + dt)), 2)
        elif part == "end":
            nf = max(od + 0.05, min(cur_d, of_ + dt))
            obj.fin = round(nf, 2) if nf < cur_d else None
        else:  # body
            span = of_ - od
            nd = max(0.0, min(cur_d - span, od + dt))
            obj.debut = round(nd, 2)
            nf = nd + span
            obj.fin = round(nf, 2) if nf < cur_d else None

        self._preview_t = obj.debut
        self._sync_timing_vars(obj.debut, obj.fin)
        self._draw_timeline()

    def _tl_obj(self, kind: str, idx: int):
        s = self.scenes[self.current]
        try:
            if kind == "narration":
                return s.narrations[idx]
            if kind == "capture":
                return s.captures[idx]
            if kind in ("arrow", "highlight"):
                return s.annotations[idx]
            if kind == "texte":
                return s.textes[idx]
            if kind == "sample":
                return s.samples[idx]
        except IndexError:
            return None
        return None

    def _tl_up(self, ev):
        self._tl_move(ev)
        self._tl_drag = None
        self._plan_apercu()

    def _tl_nudge(self, delta: float):
        """Décale finement le début du bloc sélectionné (flèches clavier).

        Respecte le verrou de durée audio (narration/sample) via `_poser_debut`
        et met à jour le champ « Début » du panneau. Sans effet sur la durée de
        scène (scene_dur) ni si rien n'est sélectionné."""
        if self.current is None or self._sel is None:
            return
        k, i = self._sel["kind"], self._sel["idx"]
        obj = self._tl_obj(k, i)
        if obj is None or not hasattr(obj, "debut"):
            return
        if self._span_verrou(k, obj) is not None:
            self._poser_debut(k, obj, obj.debut + delta)
        else:
            obj.debut = round(max(0.0, obj.debut + delta), 2)
            # ne pas dépasser la fin explicite éventuelle
            if getattr(obj, "fin", None) is not None and obj.debut > obj.fin - 0.05:
                obj.debut = round(max(0.0, obj.fin - 0.05), 2)
        self._preview_t = obj.debut
        self._sync_timing_vars(obj.debut, getattr(obj, "fin", None))
        self._draw_timeline()
        self._plan_apercu()
        return "break"

    def _sync_timing_vars(self, debut: float, fin: float | None):
        v = self._timing_vars
        if not v:
            return
        self._chargement = True
        try:
            v["debut"].set(f"{debut:.2f}")
            v["fin"].set(f"{fin:.2f}" if fin is not None else "fin")
        finally:
            self._chargement = False

    def _set_mode(self, mode: str):
        """Bascule le corps de la zone du bas entre timeline et génération."""
        self._mode = mode
        for m, b in self._mode_btns.items():
            b.config(state="disabled" if m == mode else "normal")
        if mode == "timeline":
            self.gen_wrap.pack_forget()
            self.tl_actions.pack(side="left", fill="x")
            self.tl_vsb.pack(side="right", fill="y")
            self.tl_canvas.pack(side="left", fill="both", expand=True)
            self.root.after_idle(self._draw_timeline)
        else:
            self.tl_canvas.pack_forget()
            self.tl_vsb.pack_forget()
            self.tl_actions.pack_forget()
            self.gen_wrap.pack(fill="both", expand=True)

    def _sizer_down(self, ev):
        self._sizer_y0 = ev.y_root
        self._sizer_h0 = self.tl_body.winfo_height()

    def _sizer_drag(self, ev):
        # Poignée au-dessus de la timeline : tirer vers le haut (delta négatif)
        # agrandit le corps.
        new_h = max(110, min(900, self._sizer_h0 - (ev.y_root - self._sizer_y0)))
        self.tl_body.config(height=new_h)
        if self._mode == "timeline":
            self.root.after_idle(self._draw_timeline)

    # ======================================================== BLOCK OPS
    def _exige_screenshot(self, quoi: str) -> config.Scene | None:
        if self.current is None:
            return None
        s = self.scenes[self.current]
        if s.type != "screenshot":
            messagebox.showinfo("Info",
                                f"{quoi} s'ajoute aux scènes Capture uniquement.")
            return None
        return s

    def _add_narration(self):
        if self.current is None:
            return
        s = self.scenes[self.current]
        s.narrations.append(config.Narration("Nouvelle narration.",
                                             debut=self._preview_t))
        self._select_new("narration", len(s.narrations) - 1)

    def _add_capture(self):
        s = self._exige_screenshot("Une capture")
        if s is None:
            return
        s.captures.append(config.Capture(None, debut=self._preview_t))
        self._select_new("capture", len(s.captures) - 1)

    def _add_texte(self):
        if self.current is None:
            return
        s = self.scenes[self.current]
        # Style « paragraphe » par défaut : la taille suit la taille de base.
        s.textes.append(config.TexteLibre(
            texte="Votre texte ici", x=50.0, y=78.0,
            taille=self._taille_role("paragraphe"), role="paragraphe",
            couleur="#ffffff", align="center", largeur=70.0,
            debut=self._preview_t))
        self._select_new("texte", len(s.textes) - 1)

    def _add_arrow(self):
        s = self._exige_screenshot("Une flèche")
        if s is None:
            return
        style = config.Annotation.style          # style par défaut
        taille = self.reglages.tailles_fleche.get(style, 100)
        s.annotations.append(config.Annotation(
            type="arrow", couleur=COUL["arrow"], de=(20.0, 25.0),
            vers=(60.0, 55.0), taille=taille, style=style,
            debut=self._preview_t))
        self._select_new("arrow", len(s.annotations) - 1)

    def _memo_taille_fleche(self, style: str, taille: int):
        """Mémorise la taille idéale d'un style de flèche (persisté)."""
        if self.reglages.tailles_fleche.get(style) == int(taille):
            return
        self.reglages.tailles_fleche[style] = int(taille)
        settings.sauver(self.reglages)

    def _add_highlight(self):
        s = self._exige_screenshot("Un highlight")
        if s is None:
            return
        s.annotations.append(config.Annotation(
            type="highlight", couleur=COUL["highlight"],
            zone=(30.0, 30.0, 70.0, 70.0), opacite=0.4, debut=self._preview_t))
        self._select_new("highlight", len(s.annotations) - 1)

    def _add_sample(self):
        """Ajoute un bloc sample vide ; le fichier se choisit dans le panneau."""
        if self.current is None:
            return
        s = self.scenes[self.current]
        s.samples.append(config.SampleAudio(chemin=None, debut=self._preview_t,
                                            volume=1.0))
        self._select_new("sample", len(s.samples) - 1)

    def _choisir_dossier_samples(self):
        d = filedialog.askdirectory(
            title="Dossier contenant tes samples (.wav .aiff .flac .mp3 …)",
            initialdir=str(settings.dossier_samples(self.reglages)))
        if not d:
            return
        self.reglages.samples_dir = d
        settings.sauver(self.reglages)
        self._maj_lbl_samples()
        n = len(settings.lister_samples(self.reglages))
        self._log(f"   ✓ Bibliothèque de samples : {d} ({n} fichier(s))\n")

    def _maj_lbl_samples(self):
        if not (getattr(self, "lbl_samples", None) and self.lbl_samples.winfo_exists()):
            return
        d = settings.dossier_samples(self.reglages)
        n = len(settings.lister_samples(self.reglages))
        self.lbl_samples.config(text=f"{d}  ({n} fichier·s)")

    def _select_new(self, kind: str, idx: int):
        self._sel = {"kind": kind, "idx": idx}
        self.btn_del.config(state="normal")
        self._build_settings()
        self._plan_apercu()
        self._draw_timeline()

    def _del_selected(self):
        if self.current is None or not self._sel:
            return
        s = self.scenes[self.current]
        k, i = self._sel["kind"], self._sel["idx"]
        listes = {
            "narration": s.narrations, "capture": s.captures,
            "arrow": s.annotations, "highlight": s.annotations,
            "sample": s.samples, "texte": s.textes,
        }
        lst = listes.get(k)
        if lst is None or not (0 <= i < len(lst)):
            return
        del lst[i]
        self._sel = None
        self.btn_del.config(state="disabled")
        self._build_settings()
        self._plan_apercu()
        self._draw_timeline()


