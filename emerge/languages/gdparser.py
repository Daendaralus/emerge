"""
Contains the implementation of the Python language parser and a relevant keyword enum.
"""

# Authors: Grzegorz Lato <grzegorz.lato@gmail.com>
# License: MIT

from typing import Dict, List
from enum import Enum, unique

import logging
from pathlib import Path

import coloredlogs
import pyparsing as pp

from emerge.languages.abstractparser import AbstractParser, ParsingMixin, Parser, CoreParsingKeyword, LanguageType
from emerge.results import EntityResult, FileResult
from emerge.abstractresult import AbstractResult, AbstractEntityResult
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
    CONST = "const"
    LOAD = "load"
    PRELOAD = "preload"


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
        # parent_analysis_source_path = f"{Path(analysis.source_directory).parent}/"
        # full_file_path = Path(parent_analysis_source_path) / Path(full_file_path)
        relative_file_path_to_analysis = full_file_path #str(Path(full_file_path).relative_to(parent_analysis_source_path))

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
        self._add_class_name_to_result(file_result)
        # self._add_loads_to_result(file_result)
        self._results[file_result.unique_name] = file_result

    def _add_class_name_to_result(self, result: FileResult):
        # Define parsing rules
        class_name_expr = pp.Keyword(GDParsingKeyword.PACKAGE.value) + pp.Word(pp.alphanums).setResultsName("class_name")
        extends_expr = pp.Keyword(GDParsingKeyword.EXTENDS.value) + pp.Word(pp.alphanums).setResultsName("extends_name")
        class_name = ""
        extends = ""
        # Scan lines for class_name and extends
        list_of_words = result.scanned_tokens
        for _, obj, following in self._gen_word_read_ahead(list_of_words):
            if obj == GDParsingKeyword.PACKAGE.value:
                read_ahead_string = self.create_read_ahead_string(obj, following)
                try:
                    class_name_result = class_name_expr.parseString(read_ahead_string)
                    result.module_name = class_name_result["class_name"]
                    class_name = class_name_result["class_name"]
                    LOGGER.debug(f'Found class_name: {class_name_result["class_name"]}')
                    if extends:
                        break
                    continue
                except pp.ParseException:
                    continue
            if obj == GDParsingKeyword.EXTENDS.value:
                read_ahead_string = self.create_read_ahead_string(obj, following)
                try:
                    extends_result = extends_expr.parseString(read_ahead_string)
                    result.scanned_import_dependencies.append(extends_result["extends_name"])
                    extends = extends_result["extends_name"]
                    LOGGER.debug(f'Found extends: {extends_result["extends_name"]}')
                    if class_name:
                        break
                except pp.ParseException:
                    continue
            if obj in [GDParsingKeyword.ENUM.value, GDParsingKeyword.FUNC.value, GDParsingKeyword.VAR.value, GDParsingKeyword.CONST.value, GDParsingKeyword.CLASS.value]:
                break
        if class_name and extends:
            entity_result = EntityResult(
                analysis=result.analysis,
                scanned_file_name=result.scanned_file_name,
                absolute_name=class_name,
                display_name=class_name,
                scanned_by=result.scanned_by,
                scanned_language=result.scanned_language,
                scanned_tokens=result.scanned_tokens,
                scanned_import_dependencies=[],
                entity_name=class_name,
                module_name=result.module_name,
                unique_name=class_name,
                parent_file_result=result
            )
            entity_result.scanned_inheritance_dependencies.append(extends)
            loads = self._parse_loads(result)
            entity_result.scanned_import_dependencies.extend(loads)
            self._results[f"{entity_result.unique_name}"] = entity_result

    def _parse_loads(self, result: FileResult):
        preload_or_load = pp.Or([pp.Keyword(GDParsingKeyword.LOAD.value), pp.Keyword(GDParsingKeyword.PRELOAD.value)]) + \
            pp.Suppress('(') + pp.Literal('"') + pp.NotAny("user : //") + pp.Optional(pp.Literal("res : //")).setResultsName("res") + pp.Word(pp.alphanums + "-" + '_' + "/" + ".").setResultsName("load_path") + pp.Literal('"') + pp.Suppress(')')
        list_of_words = result.scanned_tokens
        dependencies = []
        for _, obj, following in self._gen_word_read_ahead(list_of_words):
            if obj in [GDParsingKeyword.LOAD.value, GDParsingKeyword.PRELOAD.value]:
                read_ahead_string = self.create_read_ahead_string(obj, following)
                try:
                    load_result = preload_or_load.parseString(read_ahead_string)
                    if "res" not in load_result:
                        relative_load_path = result.relative_analysis_path / Path(load_result["load_path"])
                    else:
                        abs_load_path = Path(result.analysis.source_directory) / Path(load_result["load_path"])
                        relative_load_path = abs_load_path.relative_to(Path(result.analysis.source_directory).parent)
                    dependencies.append(str(relative_load_path))
                    LOGGER.debug(f'Found (pre)load: {load_result["load_path"]}')
                except pp.ParseException as e:
                    continue
        return dependencies

    def _add_loads_to_result(self, result: FileResult):
        # Scan lines for loads and preloads
        dependencies = self._parse_loads(result)
        for dep in dependencies:
            result.scanned_import_dependencies.append(dep)

    def _detect_type_usages(self, entity_result: EntityResult, entity_names: Dict[str, EntityResult]):
        # Define a pyparsing expression to match a potential type usage (identifier matching entity names)
        type_identifier = pp.Word(pp.alphas + "_", pp.alphanums + "_")
        enum_value = pp.Word(".", pp.alphanums.upper() + "_") + pp.StringEnd()
        # Define a rule that captures a dot-separated chain, but stop at method calls
        type_chain = (type_identifier + pp.ZeroOrMore(~enum_value + pp.Word(".") + type_identifier))("type")
        tokens = iter(entity_result.scanned_tokens)
        for token in tokens:
            if token in [":", "->"]:
                next_token = next(tokens, None)
                if next_token and next_token != "\n":
                    try:
                        result = type_chain.parseString(next_token)
                        type_name = "".join(result["type"])

                        # Check if the type_name is an entity in the current context
                        if type_name in entity_names and type_name != entity_result.entity_name:
                            # Add the type as a dependency if it's not already added
                            if type_name not in entity_result.scanned_import_dependencies:
                                entity_result.scanned_import_dependencies.append(type_name)
                                LOGGER.debug(f"Detected type usage: {type_name} in entity: {entity_result.entity_name}")

                    except pp.ParseException:
                        # Ignore if the token does not match an identifier
                        continue
            if token in ["="]:
                try:
                    next_token = next(tokens, None)
                    result = type_chain.parseString(next_token)
                    next_token = next(tokens, None)
                    if next_token == "(":
                        type_name = "".join(result["type"][:-2])
                    else:
                        type_name = "".join(result["type"])
                    # Check if the type_name is an entity in the current context
                    if type_name in entity_names and type_name != entity_result.entity_name:
                        # Add the type as a dependency if it's not already added
                        if type_name not in entity_result.scanned_import_dependencies:
                            entity_result.scanned_import_dependencies.append(type_name)
                            LOGGER.debug(f"Detected type usage: {type_name} in entity: {entity_result.entity_name}")

                except pp.ParseException:
                    # Ignore if the token does not match an identifier
                    continue

    def generate_entity_results_from_analysis(self, analysis):
        LOGGER.debug('generating entity results...')
        filtered_results = {k: v for (k, v) in self.results.items() if v.analysis is analysis and isinstance(v, FileResult)}

        result: FileResult
        for _, result in filtered_results.items():

            entity_keywords: List[str] = [GDParsingKeyword.CLASS.value]
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
            entity_results = result.generate_entity_results_from_scopes(entity_keywords, match_expression, comment_keywords, indent_based=True)

            entity_results: List[EntityResult]
            for entity_result in entity_results:
                self._add_inheritance_to_entity_result(entity_result)
                self.create_unique_entity_name(entity_result)
            entity_names = {entity.entity_name: entity for entity in entity_results}
            for entity_result in entity_results:
                for i, dependency in enumerate(entity_result.scanned_inheritance_dependencies):
                    if dependency in entity_names:
                        entity_result.scanned_inheritance_dependencies[i] = entity_names[dependency].unique_name
                self._results[entity_result.unique_name] = entity_result
        entity_names = {k:v for k, v in self._results.items() if isinstance(v, EntityResult)}

        for entity_result in self._results.values():
            if not isinstance(entity_result, EntityResult):
                continue
            # Detect and add type usage dependencies
            self._detect_type_usages(entity_result, entity_names)

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

    def after_generated_file_results(self, analysis) -> None:
        pass


if __name__ == "__main__":
    LEXER = GDParser()
    print(f'{LEXER.results=}')
