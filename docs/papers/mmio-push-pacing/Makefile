# Makefile for "The Missing Piece of MMIO Direct Push: Pacing"
# Keeps compiled main.pdf outside version control (or copies to /tmp).

TEX = pdflatex -interaction=nonstopmode -halt-on-error
BIB = bibtex

.PHONY: all figs clean

all: main.pdf

figs:
	/usr/bin/python3 figures/generate_all_figs.py

main.pdf: main.tex preamble.tex abstract.tex intro.tex background.tex \
          host_contention.tex device_collapse.tex design.tex evaluation.tex \
          related.tex conclusion.tex refs.bib \
          figures/fig0_architecture.pdf figures/fig1_host_tor_collapse.pdf \
          figures/fig2_device_self_collapse.pdf figures/fig3_ordering_and_doorbell.pdf \
          figures/fig4_pacing_recovery.pdf figures/fig5_e2e_apps.pdf
	$(TEX) main.tex
	$(BIB) main
	$(TEX) main.tex
	$(TEX) main.tex

clean:
	rm -f main.aux main.bbl main.blg main.log main.out main.pdf main.fls main.fdb_latexmk
