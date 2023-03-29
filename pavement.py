# -*- coding: utf-8 -*-

import os
import glob
import shutil
import platform
import fnmatch
import zipfile

from paver.easy import *


def get_extlibs():
    if platform.system() == "Windows":
        return 'extlibs_windows'
    if platform.system() == "Darwin":
        return 'extlibs_macos'
    if platform.system() == "Linux":
        return 'extlibs_linux'


def get_zip_name():
    if platform.system() == "Windows":
        return 'CCD_Plugin_Windows.zip'
    if platform.system() == "Darwin":
        return 'CCD_Plugin_MacOS.zip'
    if platform.system() == "Linux":
        return 'CCD_Plugin_Linux.zip'


def delete_directories(paths):
    """Delete directories with/without wildcards."""
    if isinstance(paths, list):
        directories = [glob.glob(p) for p in paths]
        directories = [item for sublist in directories for item in sublist]
    else:
        directories = glob.glob(paths)

    for directory in directories:
        shutil.rmtree(directory, ignore_errors=True)


options(
    plugin=Bunch(
        name='CCD_Plugin',
        ext_libs=path(get_extlibs()),
        source_dir=path('.'),
        package_dir=path('.'),
        tests=['test', 'tests'],
        excludes=[
            "*.pyc",
            ".git",
            ".github",
            ".idea",
            ".gitignore",
            "__pycache__",
            "*.zip"
        ]
    ),
)


@task
@cmdopts([('clean', 'c', 'clean out dependencies first')])
def setup():
    clean = getattr(options, 'clean', False)
    ext_libs = options.plugin.ext_libs
    if clean:
        ext_libs.rmtree()
    ext_libs.makedirs()
    reqs = read_requirements()
    os.environ['PYTHONPATH'] = ext_libs.abspath()
    for req in reqs:
        if platform.system() == "Windows":
            sh('pip install -U -t "{ext_libs}" "{dep}"'.format(ext_libs=ext_libs.abspath(), dep=req))
        else:
            sh('pip3 install -U -t "{ext_libs}" "{dep}"'.format(ext_libs=ext_libs.abspath(), dep=req))

    # remove some libraries that are not needed
    list_of_dirs = ["numpy*"]
    list_of_dirs = [os.path.join(ext_libs.abspath(), d) for d in list_of_dirs]
    delete_directories(list_of_dirs)


@task
def install(options):
    '''install plugin to qgis'''
    plugin_name = options.plugin.name
    src = path(__file__).dirname()
    if platform.system() == "Windows":
        dst = path('~/AppData/Roaming/QGIS/QGIS3/profiles/default/python/plugins').expanduser() / plugin_name
    if platform.system() == "Darwin":
        dst = path(
            '~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins').expanduser() / plugin_name
    if platform.system() == "Linux":
        dst = path('~/.local/share/QGIS/QGIS3/profiles/default/python/plugins').expanduser() / plugin_name
    src = src.abspath()
    dst = dst.abspath()
    if not hasattr(os, 'symlink'):
        dst.rmtree()
        src.copytree(dst)
    elif not dst.exists():
        src.symlink(dst)


def read_requirements():
    '''return a list of packages in requirements file'''
    with open('requirements.txt') as f:
        return [l.strip('\n') for l in f if l.strip('\n') and not l.startswith('#')]


@task
@cmdopts([('tests', 't', 'Package tests with plugin')])
def package(options):
    '''create package for plugin'''
    package_file = options.plugin.package_dir / get_zip_name()
    with zipfile.ZipFile(package_file, "w", zipfile.ZIP_DEFLATED) as f:
        if not hasattr(options.package, 'tests'):
            options.plugin.excludes.extend(options.plugin.tests)
        make_zip(f, options, src_dir=options.plugin.source_dir)

@task
def package_extlibs(options):
    '''create package for extlibs for the plugin'''
    package_file = options.plugin.package_dir / '{}.zip'.format(get_extlibs())
    with zipfile.ZipFile(package_file, "w", zipfile.ZIP_DEFLATED) as f:
        make_zip(f, options, src_dir=options.plugin.ext_libs)


def make_zip(zipFile, options, src_dir):
    excludes = set(options.plugin.excludes)

    exclude = lambda p: any([fnmatch.fnmatch(p, e) for e in excludes])

    def filter_excludes(files):
        if not files: return []
        # to prevent descending into dirs, modify the list in place
        for i in range(len(files) - 1, -1, -1):
            f = files[i]
            if exclude(f):
                files.remove(f)
        return files

    for root, dirs, files in os.walk(src_dir):
        for f in filter_excludes(files):
            relpath = os.path.relpath(root, '..').replace("CCD-Plugin", "CCD_Plugin")
            zipFile.write(path(root) / f, path(relpath) / f)
        filter_excludes(dirs)
