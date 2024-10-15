"""
Contains the implementation of the Python language parser and a relevant keyword enum.
"""

# Authors: Grzegorz Lato <grzegorz.lato@gmail.com>
# License: MIT

from typing import Dict, Set, List
from enum import Enum, unique

import logging
from pathlib import Path
import os
import sys

import pkg_resources
from pip._internal.operations.freeze import freeze

import coloredlogs
import pyparsing as pp

from emerge.languages.abstractparser import AbstractParser, ParsingMixin, Parser, CoreParsingKeyword, LanguageType
from emerge.results import EntityResult, FileResult
from emerge.abstractresult import AbstractResult, AbstractFileResult, AbstractEntityResult
from emerge.log import Logger
from emerge.stats import Statistics

LOGGER = Logger(logging.getLogger('parser'))
coloredlogs.install(level='E', logger=LOGGER.logger(), fmt=Logger.log_format)


@unique
class TSCNParsingKeyword(Enum):
    EXT_RESOURCE = "ext_resource"
    SUB_RESOURCE = "sub_resource"
    SCRIPT_TYPE = "Script"
    PACKED_SCENE_TYPE = "PackedScene"
    NODE = "node"
    PARENT = "parent"
    OBJECT_OPEN_SCOPE = "["
    OBJECT_CLOSE_SCOPE = "]"

class TSCNObject():
    def __init__(self, name: str ="", type: str ="", parent: str ="", path: str ="", id: str ="", resource_type: str =""):
        self.name = name
        self.type = type
        self.resource_type = resource_type
        self.parent = parent
        self.path = path
        self.id = id



class GodotTSCNParser(AbstractParser, ParsingMixin):

    def __init__(self):
        self._results: Dict[str, AbstractResult] = {}
        self._token_mappings: Dict[str, str] = {
            ':': ' : ',
            ';': ' ; ',
            '{': ' { ',
            '}': ' } ',
            '(': ' ( ',
            ')': ' ) ',
            '[': ' [ ',
            ']': ' ] ',
            '?': ' ? ',
            '!': ' ! ',
            ',': ' , ',
            '<': ' < ',
            '>': ' > ',
            '"': ' " '
        }

    @classmethod
    def parser_name(cls) -> str:
        return Parser.GODOT_TSCN_PARSER.name

    @classmethod
    def language_type(cls) -> str:
        return LanguageType.GODOT_TSCN.name

    @property
    def results(self) -> Dict[str, AbstractResult]:
        return self._results

    @results.setter
    def results(self, value):
        self._results = value

    def generate_file_result_from_analysis(self, analysis, *, file_name: str, full_file_path: str, file_content: str) -> None:
        LOGGER.debug('generating file results...')
        scanned_tokens = self.preprocess_file_content_and_generate_token_list_by_mapping(file_content, self._token_mappings)

        # make sure to create unique names by using the relative analysis path as a base for the result
        relative_file_path_to_analysis = full_file_path 

        file_result = FileResult.create_file_result(
            analysis=analysis,
            scanned_file_name=file_name,
            relative_file_path_to_analysis=relative_file_path_to_analysis,
            absolute_name=full_file_path,
            display_name=file_name,
            module_name=Path(relative_file_path_to_analysis).stem,
            scanned_by=self.parser_name(),
            scanned_language=LanguageType.GODOT_TSCN,
            scanned_tokens=scanned_tokens,
            source=file_content,
            preprocessed_source=""
        )
        objects = self._extract_objects(scanned_tokens, file_result)
        # self._add_scene_name(file_result, objects)
        self._add_dependencies_to_result(file_result, objects)
        self._results[file_result.unique_name] = file_result

    def _extract_objects(self, scanned_tokens: List[str], result: FileResult) -> List[TSCNObject]:
        objects = []
        LBRACK, RBRACK, EQUALS, QUOTE = map(pp.Suppress, "[]=\"")
        for _, obj, following in self._gen_word_read_ahead(scanned_tokens):
            if obj == TSCNParsingKeyword.OBJECT_OPEN_SCOPE.value:
                match following[0]:
                        case TSCNParsingKeyword.NODE.value:
                            pass
                            # read_ahead_string = self.create_read_ahead_string(obj, following)
                            # try:
                            #     node_expression = pp.Keyword(TSCNParsingKeyword.NODE.value) + node_name.setResultsName("name") + \
                            #         pp.Keyword(TSCNParsingKeyword.EXT_RESOURCE.value) + node_type.setResultsName("type") + \
                            #         pp.Keyword(TSCNParsingKeyword.PARENT.value) + node_parent.setResultsName("parent") + \
                            #         pp.Keyword(TSCNParsingKeyword.SCRIPT_TYPE.value) + node_path.setResultsName("path") + \
                            #         pp.Keyword(TSCNParsingKeyword.SUB_RESOURCE.value) + node_id.setResultsName("id") + \
                            #         pp.Keyword(TSCNParsingKeyword.PACKED_SCENE_TYPE.value) + node_resource_type.setResultsName("resource_type") + \
                            #         pp.SkipTo(pp.FollowedBy(TSCNParsingKeyword.OPEN_SCOPE.value))
                            #     parsing_result = node_expression.parseString(read_ahead_string)
                            #     objects.append(TSCNObject(parsing_result["name"], parsing_result["type"], parsing_result["parent"], parsing_result["path"], parsing_result["id"], parsing_result["resource_type"]))
                            # except pp.ParseException:
                            #     continue
                        case TSCNParsingKeyword.EXT_RESOURCE.value:
                            read_ahead_string = self.create_read_ahead_string(obj, following)
                            try:
                                resource_type = pp.QuotedString(quoteChar='"')
                                path = (
                                    pp.Literal('"')
                                    + pp.Optional(pp.Literal("res : //")).setResultsName("res") 
                                    + pp.Word(pp.alphanums + "-" + '_' 
                                    + "/" + ".").setResultsName("path") 
                                    + pp.Literal('"')
                                )
                                resource_id = pp.QuotedString(quoteChar='"')
                                grammar = (
                                    LBRACK
                                    + pp.Keyword(TSCNParsingKeyword.EXT_RESOURCE.value)
                                    + pp.Keyword("type")
                                    + EQUALS
                                    + resource_type("type")
                                    + pp.Optional("uid" + EQUALS + pp.QuotedString(quoteChar='"'))  # For uid which might not always be there
                                    + pp.Keyword("path")
                                    + EQUALS
                                    + path
                                    + pp.Keyword("id")
                                    + EQUALS
                                    + resource_id("id")
                                    + RBRACK
                                )
                                parsing_result = grammar.parseString(read_ahead_string)
                                parsing_result = {k: v.strip() for k, v in parsing_result.items()}
                                if "res" not in parsing_result:
                                    relative_path = result.relative_analysis_path / Path(parsing_result["path"])
                                else:
                                    abs_load_path = Path(result.analysis.source_directory) / Path(parsing_result["path"])
                                    relative_path = abs_load_path.relative_to(Path(result.analysis.source_directory).parent)
                                objects.append(TSCNObject(type=TSCNParsingKeyword.EXT_RESOURCE, path=str(relative_path), id=parsing_result["id"], resource_type=parsing_result["type"]))
                            except pp.ParseException as e:
                                continue

        return objects

    def after_generated_file_results(self, analysis) -> None:
        pass

    def _add_dependencies_to_result(self, result: FileResult, objects: List[TSCNObject]):
        for obj in objects:
            if obj.type == TSCNParsingKeyword.EXT_RESOURCE and obj.resource_type in [TSCNParsingKeyword.SCRIPT_TYPE.value, TSCNParsingKeyword.PACKED_SCENE_TYPE.value]:
                result.scanned_import_dependencies.append(obj.path)

    def generate_entity_results_from_analysis(self, analysis) -> None:
        pass

    def create_unique_entity_name(self, entity: AbstractEntityResult) -> None:
        pass
    
if __name__ == "__main__":
    LEXER = GodotTSCNParser()
    print(f'{LEXER.results=}')
