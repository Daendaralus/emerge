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
class GDParsingKeyword(Enum):
    INLINE_COMMENT = "#"
    NEWLINE = '\n'
    EMPTY = ''
    BLANK = ' '
    OPEN_SCOPE = ':'
    CLASS = "class"
    PACKAGE = "class_name"
    EXTENDS = "extends"
    FUNC = "func"
    ENUM = "enum"
    VAR = "var"


class GDParser(AbstractParser, ParsingMixin):

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
        return Parser.GD_PARSER.name

    @classmethod
    def language_type(cls) -> str:
        return LanguageType.GD.name

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
        parent_analysis_source_path = f"{Path(analysis.source_directory).parent}/"
        relative_file_path_to_analysis = str(Path(full_file_path).relative_to(parent_analysis_source_path))

        file_result = FileResult.create_file_result(
            analysis=analysis,
            scanned_file_name=file_name,
            relative_file_path_to_analysis=relative_file_path_to_analysis,
            absolute_name=full_file_path,
            display_name=file_name,
            module_name="",
            scanned_by=self.parser_name(),
            scanned_language=LanguageType.GD,
            scanned_tokens=scanned_tokens,
            source=file_content,
            preprocessed_source=""
        )
        self._add_class_name_and_extends_to_result(file_result)
        self._results[file_result.unique_name] = file_result

    def after_generated_file_results(self, analysis) -> None:
        # curate dependencies from the first scan java module format that actually exists, to match the real dependencies
        filtered_results = {k: v for (k, v) in self.results.items() if v.analysis is analysis and isinstance(v, FileResult)}

        result: FileResult
        for _, result in filtered_results.items():
            curated_dependencies = []

            for dependency in result.scanned_import_dependencies:
                curated = False
                needle = str(Path("/"+dependency.replace(".", "/") + ".gd"))

                for haystack, v in filtered_results.items():
                    if needle in haystack:
                        curated = True
                        curated_dependencies.append(haystack)
                        break
                if not curated:
                    curated_dependencies.append(dependency)

            result.scanned_import_dependencies = curated_dependencies

    def _add_class_name_and_extends_to_result(self, result: FileResult):
        # Define parsing rules
        class_name_expr = pp.Keyword(GDParsingKeyword.PACKAGE.value) + pp.Word(pp.alphanums).setResultsName("class_name")
        extends_expr = pp.Keyword(GDParsingKeyword.EXTENDS.value) + pp.Word(pp.alphanums).setResultsName("extends_name")

        # Scan lines for class_name and extends
        list_of_words = result.scanned_tokens
        for _, obj, following in self._gen_word_read_ahead(list_of_words):
            if obj == GDParsingKeyword.PACKAGE.value:
                read_ahead_string = self.create_read_ahead_string(obj, following)
                try:
                    class_name_result = class_name_expr.parseString(read_ahead_string)
                    result.module_name = class_name_result["class_name"]
                    LOGGER.debug(f'Found class_name: {class_name_result["class_name"]}')
                except pp.ParseException:
                    continue
            if obj == GDParsingKeyword.EXTENDS.value:
                read_ahead_string = self.create_read_ahead_string(obj, following)
                try:
                    extends_result = extends_expr.parseString(read_ahead_string)
                    result.scanned_import_dependencies.append(extends_result["extends_name"])
                    LOGGER.debug(f'Found extends: {extends_result["extends_name"]}')
                except pp.ParseException:
                    continue
            if obj in [GDParsingKeyword.ENUM.value, GDParsingKeyword.FUNC.value, GDParsingKeyword.VAR.value]:
                break

    def generate_entity_results_from_analysis(self, analysis):
        LOGGER.debug('generating entity results...')
        filtered_results = {k: v for (k, v) in self.results.items() if v.analysis is analysis and isinstance(v, FileResult)}

        result: FileResult
        for _, result in filtered_results.items():

            entity_keywords: List[str] = [GDParsingKeyword.CLASS.value, GDParsingKeyword.PACKAGE.value, GDParsingKeyword.EXTENDS.value]
            entity_name = pp.Word(pp.alphanums)
            
            entity_expression = pp.Or([pp.Keyword(kw) for kw in entity_keywords]) + \
                entity_name.setResultsName(CoreParsingKeyword.ENTITY_NAME.value)
            inheritance_expression = pp.Optional(pp.Keyword(GDParsingKeyword.EXTENDS.value) + 
                                                 entity_name.setResultsName(CoreParsingKeyword.INHERITED_ENTITY_NAME.value))
            match_expression = pp.Or([entity_expression + inheritance_expression, inheritance_expression + entity_expression]) + \
                pp.Optional(pp.SkipTo(pp.FollowedBy(GDParsingKeyword.OPEN_SCOPE.value)))
            comment_keywords: Dict[str, str] = {
                CoreParsingKeyword.LINE_COMMENT.value: GDParsingKeyword.INLINE_COMMENT.value,
                CoreParsingKeyword.START_BLOCK_COMMENT.value: "",
                CoreParsingKeyword.STOP_BLOCK_COMMENT.value: ""
            }
            entity_results = result.generate_entity_results_from_scopes(entity_keywords, match_expression, comment_keywords)

            entity_results: List[EntityResult]
            for entity_result in entity_results:
                self._add_inheritance_to_entity_result(entity_result)
                self.create_unique_entity_name(entity_result)
                self._results[entity_result.unique_name] = entity_result

    def create_unique_entity_name(self, entity: AbstractEntityResult) -> None:
        if entity.module_name:
            entity.unique_name = entity.module_name + CoreParsingKeyword.DOT.value + entity.entity_name
        else:
            entity.unique_name = entity.entity_name

    def _add_inheritance_to_entity_result(self, result: AbstractEntityResult):
        LOGGER.debug(f'extracting inheritance from entity result {result.entity_name}...')
        list_of_words = result.scanned_tokens
        for _, obj, following in self._gen_word_read_ahead(list_of_words):
            if obj == GDParsingKeyword.CLASS.value:
                read_ahead_string = self.create_read_ahead_string(obj, following)

                entity_name = pp.Word(pp.alphanums)
                expression_to_match = pp.Keyword(GDParsingKeyword.CLASS.value) + entity_name.setResultsName(CoreParsingKeyword.ENTITY_NAME.value) + \
                    pp.Optional(pp.Keyword(GDParsingKeyword.EXTENDS.value) + entity_name.setResultsName(CoreParsingKeyword.INHERITED_ENTITY_NAME.value)) + \
                    pp.SkipTo(pp.FollowedBy(GDParsingKeyword.OPEN_SCOPE.value))

                try:
                    parsing_result = expression_to_match.parseString(read_ahead_string)
                except pp.ParseException as exception:
                    result.analysis.statistics.increment(Statistics.Key.PARSING_MISSES)
                    LOGGER.warning(f'warning: could not parse result {result=}\n{exception}')
                    LOGGER.warning(f'next tokens: {obj} {following[:10]}')
                    continue

                if len(parsing_result) > 0:
                    parsing_result = expression_to_match.parseString(read_ahead_string)

                    if getattr(parsing_result, CoreParsingKeyword.INHERITED_ENTITY_NAME.value) is not None and \
                    bool(getattr(parsing_result, CoreParsingKeyword.INHERITED_ENTITY_NAME.value)):

                        result.analysis.statistics.increment(Statistics.Key.PARSING_HITS)
                        LOGGER.debug(
                            f'found inheritance entity {getattr(parsing_result, CoreParsingKeyword.INHERITED_ENTITY_NAME.value)} ' +
                            'for entity name: {getattr(parsing_result, CoreParsingKeyword.ENTITY_NAME.value)} and added to result')
                        result.scanned_inheritance_dependencies.append(getattr(parsing_result, CoreParsingKeyword.INHERITED_ENTITY_NAME.value))





if __name__ == "__main__":
    LEXER = GDParser()
    print(f'{LEXER.results=}')
