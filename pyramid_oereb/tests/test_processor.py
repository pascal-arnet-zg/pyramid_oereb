# -*- coding: utf-8 -*-
import pytest
from pyramid_oereb.lib.processor import Processor
from pyramid_oereb.tests.conftest import MockRequest
from pyramid_oereb import ExtractReader
from pyramid_oereb import MunicipalityReader
from pyramid_oereb import ExclusionOfLiabilityReader
from pyramid_oereb import GlossaryReader
from pyramid_oereb import RealEstateReader


def test_missing_params():
    with pytest.raises(TypeError):
        Processor()


def test_properties():
    request = MockRequest()
    processor = request.pyramid_oereb_processor
    assert isinstance(processor.extract_reader, ExtractReader)
    assert isinstance(processor.municipality_reader, MunicipalityReader)
    assert isinstance(processor.exclusion_of_liability_reader, ExclusionOfLiabilityReader)
    assert isinstance(processor.glossary_reader, GlossaryReader)
    assert isinstance(processor.plr_sources, list)
    assert isinstance(processor.real_estate_reader, RealEstateReader)


def test_process(connection):
    assert connection.closed
    request = MockRequest()
    processor = request.pyramid_oereb_processor
    real_estate = processor.real_estate_reader.read(egrid=u'TEST')
    extract = processor.process(real_estate[0])
    assert isinstance(extract.to_extract(), dict)
