#/***************************************************************************
# CCD Plugin
#
# Continuous Change Detection Plugin
#                             -------------------
#        copyright            : (C) 2019 by Xavier Corredor Llano, SMByC
#        email                : xcorredorl@ideam.gov.co
# ***************************************************************************/
#
#/***************************************************************************
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU General Public License as published by  *
# *   the Free Software Foundation; either version 2 of the License, or     *
# *   (at your option) any later version.                                   *
# *                                                                         *
# ***************************************************************************/

#################################################
# Edit the following to match your sources lists
#################################################


#Add iso code for any locales you want to support here (space separated)
# default is no locales
# LOCALES = af
LOCALES =

# translation
SOURCES = \
    __init__.py \
    CCD_Plugin.py

PLUGINNAME = CCD_Plugin

PY_FILES = \
    __init__.py \
    CCD_Plugin.py

UI_FILES =

EXTRAS = metadata.txt LICENSE Readme.md screenshot.webp

EXTRA_DIRS = core gui icons ui utils

COMPILED_RESOURCE_FILES = resources.py

RESOURCE_SRC=$(shell grep '^ *<file' resources.qrc | sed 's@</file>@@g;s/.*>//g' | tr '\n' ' ')

QT6_RCC ?= $(firstword $(wildcard /usr/lib/qt6/rcc /usr/lib64/qt6/rcc) $(shell command -v rcc 2>/dev/null))
QGIS ?= qgis

.PHONY: extlibs

PEP8EXCLUDE=pydev,resources.py,conf.py,third_party,ui

# QGISDIR points to the location where your plugin should be installed.
# This varies by platform, relative to your HOME directory:
#	* Linux:
#	  .local/share/QGIS/QGIS4/profiles/default
#	* Mac OS X:
#	  Library/Application Support/QGIS/QGIS4/profiles/default
#	* Windows:
#	  AppData\Roaming\QGIS\QGIS4\profiles\default'

QGISDIR=.local/share/QGIS/QGIS4/profiles/default

#################################################
# Normally you would not need to edit below here
#################################################

HELP = Readme.md

default: compile

compile: $(COMPILED_RESOURCE_FILES)

resources.py: resources.qrc $(RESOURCE_SRC)
	@if [ -z "$(QT6_RCC)" ] || ! "$(QT6_RCC)" --version 2>&1 | grep -q ' 6\.'; then \
		echo "ERROR: Qt 6 rcc not found. Set QT6_RCC to the Qt 6 resource compiler."; \
		exit 1; \
	fi
	$(QT6_RCC) -g python -o $@ $<
	sed -i 's/^from PySide6 import QtCore$$/from qgis.PyQt import QtCore/' $@

%.qm : %.ts
	$(LRELEASE) $<

test: compile
	PYTHONPATH="$(CURDIR):$(CURDIR)/extlibs" uv run --no-project --with-requirements requirements.txt --with numpy python -m unittest discover -s tests -p 'test_*.py' -v

qgis-smoke: compile
	CCD_RUN_QGIS4_SMOKE=1 QTWEBENGINE_DISABLE_SANDBOX=1 \
		$(QGIS) --nologo --noplugins --code tests/run_qgis4_webengine_smoke.py

deploy: compile doc transcompile
	@echo
	@echo "------------------------------------------"
	@echo "Deploying plugin to your QGIS 4 directory."
	@echo "------------------------------------------"
	# The deploy  target only works on unix like operating system where
	# the Python plugin directory is located at:
	# $HOME/$(QGISDIR)/python/plugins
	mkdir -p $(HOME)/$(QGISDIR)/python/plugins/$(PLUGINNAME)
	cp -vf $(PY_FILES) $(COMPILED_RESOURCE_FILES) $(HOME)/$(QGISDIR)/python/plugins/$(PLUGINNAME)
	#cp -vf $(UI_FILES) $(HOME)/$(QGISDIR)/python/plugins/$(PLUGINNAME)
	cp -vf $(EXTRAS) $(HOME)/$(QGISDIR)/python/plugins/$(PLUGINNAME)
	#cp -vfr i18n $(HOME)/$(QGISDIR)/python/plugins/$(PLUGINNAME)
	cp -vfr $(HELP) $(HOME)/$(QGISDIR)/python/plugins/$(PLUGINNAME)/help
	# Copy extra directories if any
	cp -vfr $(EXTRA_DIRS) $(HOME)/$(QGISDIR)/python/plugins/$(PLUGINNAME)


# The dclean target removes compiled python files from plugin directory
# also deletes any .git entry
dclean:
	@echo
	@echo "-----------------------------------"
	@echo "Removing any compiled python files."
	@echo "-----------------------------------"
	find $(HOME)/$(QGISDIR)/python/plugins/$(PLUGINNAME) -iname "*.pyc" -delete
	find $(HOME)/$(QGISDIR)/python/plugins/$(PLUGINNAME) -iname ".git" -prune -exec rm -Rf {} \;
	find $(HOME)/$(QGISDIR)/python/plugins/$(PLUGINNAME) -iname "__pycache__" -prune -exec rm -Rf {} \;


derase:
	@echo
	@echo "-------------------------"
	@echo "Removing deployed plugin."
	@echo "-------------------------"
	rm -Rf $(HOME)/$(QGISDIR)/python/plugins/$(PLUGINNAME)

extlibs:
	@echo
	@echo "---------------------------"
	@echo "Building extlibs.zip"
	@echo "---------------------------"
	rm -rf extlibs extlibs.zip
	uv pip install --target=extlibs -r requirements.txt
	PLOTLY_VERSION=$$(PYTHONPATH="$(CURDIR)/extlibs" python3 -c "from importlib.metadata import version; print(version('plotly'))"); \
		sed -i "/^import importlib\.metadata$$/d;s/^__version__ = importlib\.metadata\.version(\"plotly\")$$/__version__ = \"$${PLOTLY_VERSION}\"/" extlibs/plotly/__init__.py
	find extlibs -type d \( -name "__pycache__" -o -name "*.dist-info" -o -name "*.egg-info" -o -name "tests" -o -name "test" -o -name "bin" -o -name "examples" \) -prune -exec rm -rf {} +
	find extlibs -type f \( -name ".lock" -o -name "*.pyc" -o -name "*.pyo" -o -name "*.so" -o -name "*.dll" -o -name "*.dylib" \) -delete
	# plotly's Jupyter payload: ~14 MB of notebook widget bundles this plugin never loads
	rm -rf extlibs/share extlibs/plotly/labextension extlibs/plotly/package_data/widgetbundle.js
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$(CURDIR)/extlibs" python3 -c "import plotly, plotly.graph_objects; assert plotly.__version__"
	cd extlibs && zip -9r ../extlibs.zip .
	rm -rf extlibs
	@echo "Created package: extlibs.zip"

zip: compile
	@echo
	@echo "---------------------------"
	@echo "Creating plugin zip bundle."
	@echo "---------------------------"
	rm -f $(PLUGINNAME).zip
	mkdir -p .pkg_tmp/$(PLUGINNAME)
	cp -f $(PY_FILES) $(COMPILED_RESOURCE_FILES) $(EXTRAS) .pkg_tmp/$(PLUGINNAME)/
	@for d in $(EXTRA_DIRS); do \
		if [ -d "$$d" ]; then cp -rf $$d .pkg_tmp/$(PLUGINNAME)/; fi; \
	done
	find .pkg_tmp -type d \( -name "__pycache__" -o -name "*.dist-info" -o -name "*.egg-info" \) -prune -exec rm -rf {} \;
	find .pkg_tmp -type f \( -name "*.pyc" -o -name "*.pyo" -o -name "*.sh"  -o -name "*.db" \) -delete
	cd .pkg_tmp && zip -9r ../$(PLUGINNAME).zip $(PLUGINNAME)
	rm -rf .pkg_tmp
	@echo "Created package: $(PLUGINNAME).zip"

check-package: zip
	@unzip -p $(PLUGINNAME).zip $(PLUGINNAME)/metadata.txt | grep -q '^qgisMinimumVersion=4.0$$'
	@if unzip -p $(PLUGINNAME).zip $(PLUGINNAME)/metadata.txt | grep -Eq 'qgisMaximumVersion|supportsQt6'; then exit 1; fi
	@if unzip -l $(PLUGINNAME).zip | grep -Eq 'QWebView|QtWebKit|__pycache__|\.pyc$$'; then exit 1; fi
	@if unzip -p $(PLUGINNAME).zip $(PLUGINNAME)/resources.py | grep -Eq 'PyQt5|Qt v5'; then exit 1; fi
	@if unzip -p $(PLUGINNAME).zip $(PLUGINNAME)/Readme.md | grep -Eq 'QGIS 3|Qt5|QtWebKit'; then exit 1; fi
	@unzip -l $(PLUGINNAME).zip | grep -q '$(PLUGINNAME)/screenshot.webp'
	@echo "Package is QGIS 4 / Qt 6 only."

transup:
	@echo
	@echo "------------------------------------------------"
	@echo "Updating translation files with any new strings."
	@echo "------------------------------------------------"
	@chmod +x scripts/update-strings.sh
	@scripts/update-strings.sh $(LOCALES)

transcompile:
	@echo
	@echo "----------------------------------------"
	@echo "Compiled translation files to .qm files."
	@echo "----------------------------------------"

transclean:
	@echo
	@echo "------------------------------------"
	@echo "Removing compiled translation files."
	@echo "------------------------------------"
	rm -f i18n/*.qm

clean:
	@echo
	@echo "------------------------------------"
	@echo "Removing generated files"
	@echo "------------------------------------"
	rm -f $(COMPILED_RESOURCE_FILES)
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

doc:
	@echo
	@echo "------------------------------------"
	@echo "Building documentation using sphinx."
	@echo "------------------------------------"
	# cd help; make html

ruff:
	@echo
	@echo "-------------------"
	@echo "Ruff code quality"
	@echo "-------------------"
	@ruff check .
	@echo "-------------------"
	@echo "No issues found."

ruff-format:
	@echo
	@echo "-------------------"
	@echo "Ruff format"
	@echo "-------------------"
	@ruff format .
	@echo "-------------------"
	@echo "Done."
