#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# --------------------------------------------------------------------------------
# "THE BEER-WARE LICENSE" (Revision 42):

# <martin.groenholdt@gmail.com> wrote this file. As long as you retain this notice
# you can do whatever you want with this stuff. If we meet some day, and you think
# this stuff is worth it, you can buy me a beer in return. Martin B. K. Grønholdt
# --------------------------------------------------------------------------------
"""
Program to parse Python classes and write their info to PlantUML files
(see http://plantuml.com/) that can be used to generate UML files and GraphViz
renderings of classes.

Missing:
 * Inheritance parsing
"""
# pylint: disable=C0103

# standard lib imports
import ast
import configparser
import logging
import logging.config
import os
import sys

# other imports
import astor
import yaml

__VERSION__ = '0.2.1'
HOME_DIR = os.path.dirname(__file__)

# puml indentation unit
TAB = '  '

# list of possible configuration files,
# all existing will be loaded unless a specific config arg is provided
CONFIG_FILENAMES = (
    os.path.join(HOME_DIR, 'py2puml.ini'),
    os.path.expanduser('~/.config/py2puml.ini'),
    os.path.expanduser('~/.py2puml.ini'),
    '.py2puml.ini',
    'py2puml.ini',
)
LOGGING_CFG = os.path.join(HOME_DIR, 'logging.yaml')

with open(LOGGING_CFG) as f:
    logging.config.dictConfig(yaml.load(f))
logger = logging.getLogger(__name__)

class ClassInfo:
    """
    The parsed class definition.

    Elements are grouped by type in arrays while parsing, printing done last.
    """
    def __init__(self, node):
        self.classname = node.name
        self.bases = node.bases
        self.classvars = []
        self.members = []
        self.methods = []

    @staticmethod
    def visibility(name):
        """Detects the visibility of given member name, as plantuml convention symbol.

       @returns '-' for private, '+' for public
        """
        if name.startswith('_'):
            return '-'
        return '+'

    def add_classvar(self, name):
        "Registers a class variable"
        self.classvars.append(name)

    def add_member(self, name):
        "Registers an instance variable if new"
        if name not in self.members:
            self.members.append(name)

    def add_method(self, node):
        "Registers a method"
        self.methods.append(node)

    def done(self, context):
        "Signals end of class parsing."
        context.print_classinfo(self)

class TreeVisitor(ast.NodeVisitor):
    """`ast.NodeVisitor` is the primary tool for ‘scanning’ the tree.
    To use it, subclass it and override methods visit_Foo, corresponding to the
    node classes (see Meet the Nodes).
    """
    # List to put the class data.
    def __init__(self, context=None):
        self.context = context
        self.classinfo = None
        self.constructor = False

    def visit_ClassDef(self, node):
        """
        Overrides AST class definition visitor.

        :param node: The node of the class.
        """
        # push context
        prev_classinfo = self.classinfo
        self.classinfo = ClassInfo(node)

        # Run through all children of the class definition.
        for child in node.body:
            self.visit(child)

        # finished class parsing, report it now.
        self.classinfo.done(self.context)

        # restore previous context
        self.classinfo = prev_classinfo

    def visit_FunctionDef(self, node):
        "Overrides AST function definition visitor"
        if self.classinfo:
            # Check if this s the constructor.
            if node.name == '__init__':
                self.constructor = True
                # Find all assignment expressions in the constructor.
                for code in node.body:
                    self.visit(code)
                self.constructor = False
            self.classinfo.add_method(node)

    def visit_Assign(self, node):
        "Overrides AST assignment statement visitor"
        if self.constructor:
            # Find attributes since we want "self." + "something"
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    # If the value is a name and its id is self.
                    if isinstance(target.value, ast.Name):
                        if target.value.id == 'self':
                            self.classinfo.add_member(target.attr)

        elif self.classinfo:
            # Look for class variables (shared by all instances)
            for target in node.targets:
                # Get the target member name.
                if isinstance(target, ast.Name):
                    self.classinfo.add_classvar(target.id)

class PUML_Generator:
    """Formats data for PlantUML.
    """
    # TODO Extract a derived class for handling namespaces
    def __init__(self, dest, root='', config=None):
        self.dest = dest
        self.root = root
        self.config = config
        # self.tabs = 0
        self.curpath = ''
        self.namespaces = []
        self.sourcename = None

    def start_file(self, sourcename):
        "Sets up the output context for a single python source file"
        self.sourcename = sourcename
        # make namespace hierarchy from root if supplied
        if self.root:
            names = os.path.splitext(os.path.relpath(sourcename, self.root))[0]
            namespaces = names.split(os.path.sep)

            # determine the common path
            n = 0
            for d in self.namespaces:
                if n >= len(namespaces):
                    break
                if d != namespaces[n]:
                    break
                n += 1
            self.pop_ns(len(self.namespaces) - n)

            for d in namespaces[n:]:
                self.push_ns(d)

    def end_file(self, sourcename=None):
        "Cleans up the output context for a single python source file"
        logger.info('finished with %s', sourcename)
        self.sourcename = None

    def pop_ns(self, count=1):
        "Removes some inner namespaces"
        for n in range(count): # pylint: disable=unused-variable
            self.namespaces.pop()
            self.indent('}')

    def push_ns(self, name):
        "Adds an inner namespace to the context"
        self.indent('namespace ' + name + ' {')
        self.namespaces.append(name)

    @property
    def depth(self):
        "Levels of current namespace nesting"
        return len(self.namespaces)

    def indent(self, *args):
        "Formats given arguments to destination with proper indentation."
        if self.namespaces:
            print(TAB * self.depth, end="", file=self.dest)
        print(*args, file=self.dest)

    def header(self):
        "Outputs file header: settings and namespaces."
        self.indent("@startuml")
        if self.config:
            prolog = self.config.get('puml', 'prolog', fallback=None)
            if prolog:
                self.indent(prolog + "\n")

    def footer(self):
        "Outputs file footer: close namespaces and marker."
        # Close the namespaces
        while self.namespaces:
            self.pop_ns(self.depth)

        # append the epilog if provided
        if self.config:
            epilog = self.config.get('puml', 'epilog', fallback=None)
            if epilog:
                self.indent(epilog + "\n")

        # End the PlantUML files.
        self.indent('@enduml')


    def print_classinfo(self, classinfo):
        """Prints class definition as plantuml script."""
        for base in classinfo.bases:
            expr = astor.to_source(base).rstrip()
            # ignore base if 'object'
            if expr != 'object':
                self.indent(expr, "<|--", classinfo.classname)
        # class and instance members
        self.indent("class", classinfo.classname, "{")
        for m in classinfo.classvars:
            self.indent(TAB + "{static}", classinfo.visibility(m) + m)
        for m in classinfo.members:
            self.indent(TAB + classinfo.visibility(m) + m)
        for m in classinfo.methods:
            logger.info("Method: %r \n%s", m, ast.dump(m))
            arglist = astor.to_source(m.args).rstrip()
            self.indent(TAB + "{0}{1}({2})".format(
                classinfo.visibility(m.name),
                m.name, arglist))
        self.indent("}\n")
        # TODO allow to disable or simplify arg lists output (config)

def cli_parser():
    "Builds a command line parser suitable for this tool."
    import argparse

    # Takes a python file as a parameter.
    parser = argparse.ArgumentParser(
        prog='py2uml',
        description='py2puml' +
        ' from Martin B. K. Grønholdt, v' + __VERSION__ + ' by Michelle Baert.\n' +
        'Create PlantUML classes from Python source code.',
        epilog='If no config file is provided, settings are loaded \n' +
        'sequentially from all available files in :\n' +
        '      - <PROGRAM_DIR>/py2puml.ini\n' +
        '      - <USER_HOME>/.config/py2puml.ini\n' +
        '      - <USER_HOME>/.py2puml.ini\n' +
        '      - <WORK_DIR>/.py2puml.ini\n' +
        '      - <WORK_DIR>/py2puml.ini\n',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config',
                        help='Configuration file (replace defaults)')
    parser.add_argument('-o', '--output', type=argparse.FileType('w'),
                        nargs='?', default=sys.stdout,
                        help='The name of the ouput PlantUML file.')
    parser.add_argument('-r', '--root', #default='',
                        help='Project root directory.'
                        ' Create namespaces from there')
    parser.add_argument('py_file', nargs='+',
                        help='the Python source files to parse.')
    return parser

if __name__ == '__main__':
    cfg = configparser.ConfigParser()
    cl_args = cli_parser().parse_args()
    try:
        # provided config file completely replaces global settings
        if cl_args.config:
            cfg.read(cl_args.config)
        else:
            cfg.read(CONFIG_FILENAMES)

        # setup .puml generator
        gen = PUML_Generator(dest=cl_args.output,
                             root=cl_args.root,
                             config=cfg)

        # The tree visitor will use it
        visitor = TreeVisitor(gen)

        # Build output while walking the tree
        gen.header()
        for srcfile in cl_args.py_file:
            gen.start_file(srcfile)
            # Use AST to parse the file.
            with open(srcfile) as src:
                tree = ast.parse(src.read())
            visitor.visit(tree)
            gen.end_file(srcfile)

        gen.footer()
        # TODO detect and warn about empty results
        # TODO optionally include global objects in a pseudo-class
        cl_args.output.close()

    except SyntaxError as see:
        print('Syntax error in ', end='')
        print(cl_args.py_file.name + ':' + str(see.lineno) + ':' + str(
            see.offset) + ': ' + see.text)
    # except BaseException as err:
    #     print(err)
