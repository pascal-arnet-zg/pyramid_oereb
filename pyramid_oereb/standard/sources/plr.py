# -*- coding: utf-8 -*-
import base64
import logging

from geoalchemy2.shape import to_shape, from_shape
from pyramid.path import DottedNameResolver
from shapely.geometry import Point, LineString, Polygon, MultiPoint, MultiLineString, MultiPolygon, \
    GeometryCollection
from sqlalchemy import text, or_

from pyramid_oereb import Config
from pyramid_oereb.lib.records.availability import AvailabilityRecord
from pyramid_oereb.lib.records.embeddable import DatasourceRecord
from pyramid_oereb.lib.records.image import ImageRecord
from pyramid_oereb.lib.records.law_status import LawStatusRecord
from pyramid_oereb.lib.records.office import OfficeRecord
from pyramid_oereb.lib.records.plr import EmptyPlrRecord
from pyramid_oereb.lib.records.theme import ThemeRecord
from pyramid_oereb.lib.sources import BaseDatabaseSource
from pyramid_oereb.lib.sources.plr import PlrBaseSource

log = logging.getLogger(__name__)


class DatabaseSource(BaseDatabaseSource, PlrBaseSource):
    def __init__(self, **kwargs):
        """
        Keyword Arguments:
            name (str): The name. You are free to choose one.
            code (str): The official code. Regarding to the federal specifications.
            geometry_type (str): The geometry type. Possible are: POINT, POLYGON, LINESTRING,
                GEOMETRYCOLLECTION
            thresholds (dict): The configuration of limits, units and precision which is used for processing.
            text (dict of str): The speaking title. It must be a dictionary containing language (as
                configured) as key and text as value.
            language (str): The language this public law restriction is originally shipped whith.
            federal (bool): Switch if it is a federal topic. This will be taken into account in processing
                steps.
            source (dict): The configuration dictionary of the public law restriction
            hooks (dict of str): The hook methods: get_symbol, get_symbol_ref. They have to be provided as
                dotted string for further use with dotted name resolver of pyramid package.
            law_status (dict of str): The multiple match configuration to provide more flexible use of the
                federal specified classifiers 'inForce' and 'runningModifications'.
        """
        models_path = kwargs.get('source').get('params').get('models')
        bds_kwargs = {
            'model': DottedNameResolver().maybe_resolve(
                '{models_path}.Geometry'.format(models_path=models_path)
            ),
            'db_connection': kwargs.get('source').get('params').get('db_connection')
        }

        BaseDatabaseSource.__init__(self, **bds_kwargs)
        PlrBaseSource.__init__(self, **kwargs)

        self.legend_entry_model = DottedNameResolver().maybe_resolve(
            '{models_path}.LegendEntry'.format(models_path=models_path)
        )
        availability_model = DottedNameResolver().maybe_resolve(
            '{models_path}.Availability'.format(models_path=models_path)
        )
        data_integration_model = DottedNameResolver().maybe_resolve(
            '{models_path}.DataIntegration'.format(models_path=models_path)
        )

        self._theme_record = ThemeRecord(self._plr_info.get('code'), self._plr_info.get('text'))

        self.availabilities = []
        self.datasource = []

        session = self._adapter_.get_session(self._key_)

        try:

            availabilities_from_db = session.query(availability_model).all()
            for availability in availabilities_from_db:
                self.availabilities.append(
                    AvailabilityRecord(availability.fosnr, available=availability.available)
                )

            data_integration = session.query(data_integration_model).all()
            for source in data_integration:
                self.datasource.append(DatasourceRecord(
                    self._theme_record,
                    source.date,
                    OfficeRecord(source.office.name)
                ))

        finally:
            session.close()

    def from_db_to_legend_entry_record(self, theme, legend_entries_from_db):
        legend_entry_records = []
        for legend_entry_from_db in legend_entries_from_db:
            legend_entry_records.append(self._legend_entry_record_class(
                ImageRecord(base64.b64decode(legend_entry_from_db.symbol)),
                legend_entry_from_db.legend_text,
                legend_entry_from_db.type_code,
                legend_entry_from_db.type_code_list,
                theme,
                sub_theme=legend_entry_from_db.sub_theme,
                other_theme=legend_entry_from_db.other_theme
            ))
        return legend_entry_records

    def from_db_to_view_service_record(self, view_service_from_db, legend_entry_records):
        view_service_record = self._view_service_record_class(
            view_service_from_db.reference_wms,
            view_service_from_db.legend_at_web,
            legends=legend_entry_records
        )
        return view_service_record

    def unwrap_multi_geometry_(self, law_status, published_from, multi_geom, geo_metadata, office):
        unwrapped = []
        for geom in multi_geom.geoms:
            unwrapped.append(
                self._geometry_record_class(
                    law_status,
                    published_from,
                    geom,
                    geo_metadata,
                    office=office
                )
            )
        return unwrapped

    def unwrap_geometry_collection_(self, law_status, published_from, collection, geo_metadata, office):
        unwrapped = []
        if len(collection.geoms) > 1:
            raise AttributeError(u'There was more than one element in the GeometryCollection. '
                                 u'This is not supported!')
        for geom in collection.geoms:
            unwrapped.extend(
                self.create_geometry_records_(
                    law_status,
                    published_from,
                    geom,
                    geo_metadata,
                    office=office
                )
            )
        return unwrapped

    def create_geometry_records_(self, law_status, published_from, geom, geo_metadata, office):
        geometry_records = []

        # Process single geometries
        if isinstance(geom, (Point, LineString, Polygon)):
            geometry_records.append(
                self._geometry_record_class(
                    law_status,
                    published_from,
                    geom,
                    geo_metadata,
                    office=office
                )
            )

        # Process multi geometries
        elif isinstance(geom, (MultiPoint, MultiLineString, MultiPolygon)):
            geometry_records.extend(self.unwrap_multi_geometry_(
                law_status,
                published_from,
                geom,
                geo_metadata,
                office
            ))

        # Process geometry collections
        elif isinstance(geom, GeometryCollection):
            geometry_records.extend(self.unwrap_geometry_collection_(
                law_status,
                published_from,
                geom,
                geo_metadata,
                office
            ))

        else:
            log.warning('Received geometry with unsupported type: {0}. Skipped geometry.'.format(
                geom.type
            ))

        return geometry_records

    def from_db_to_geometry_records(self, geometries_from_db):
        geometry_records = []
        for geometry_from_db in geometries_from_db:

            # Create law status record
            law_status = LawStatusRecord.from_config(
                Config.get_law_status(
                    self._plr_info.get('code'),
                    self._plr_info.get('law_status'),
                    geometry_from_db.law_status
                )
            )

            # Create office record
            office = self.from_db_to_office_record(geometry_from_db.responsible_office)

            # Create geometry records
            geometry_records.extend(self.create_geometry_records_(
                law_status,
                geometry_from_db.published_from,
                to_shape(geometry_from_db.geom),
                geometry_from_db.geo_metadata,
                office
            ))

        return geometry_records

    def from_db_to_office_record(self, offices_from_db):
        office_record = self._office_record_class(
            offices_from_db.name,
            offices_from_db.uid,
            offices_from_db.office_at_web,
            offices_from_db.line1,
            offices_from_db.line2,
            offices_from_db.street,
            offices_from_db.number,
            offices_from_db.postal_code,
            offices_from_db.city
        )
        return office_record

    def from_db_to_article_records(self, articles_from_db):
        article_records = []
        for article_from_db in articles_from_db:
            law_status = LawStatusRecord.from_config(
                Config.get_law_status(
                    self._plr_info.get('code'),
                    self._plr_info.get('law_status'),
                    article_from_db.law_status
                )
            )
            article_records.append(self._article_record_class(
                law_status,
                article_from_db.published_from,
                article_from_db.number,
                article_from_db.text_at_web,
                article_from_db.text
            ))
        return article_records

    def from_db_to_document_records(self, legal_provisions_from_db, article_numbers=None):
        document_records = []
        for i, legal_provision in enumerate(legal_provisions_from_db):
            referenced_documents_db = []
            referenced_article_numbers = []
            for join in legal_provision.referenced_documents:
                referenced_documents_db.append(join.referenced_document)
                referenced_article_nrs = join.article_numbers.split('|') if join.article_numbers else None
                referenced_article_numbers.append(referenced_article_nrs)
            referenced_document_records = self.from_db_to_document_records(
                referenced_documents_db,
                referenced_article_numbers
            )
            article_records = self.from_db_to_article_records(legal_provision.articles)
            office_record = self.from_db_to_office_record(legal_provision.responsible_office)
            article_nrs = article_numbers[i] if isinstance(article_numbers, list) else None
            law_status = LawStatusRecord.from_config(
                Config.get_law_status(
                    self._plr_info.get('code'),
                    self._plr_info.get('law_status'),
                    legal_provision.law_status
                )
            )
            document_records.append(self._documents_reocord_class(
                law_status,
                legal_provision.published_from,
                legal_provision.title,
                office_record,
                legal_provision.text_at_web,
                legal_provision.abbreviation,
                legal_provision.official_number,
                legal_provision.official_title,
                legal_provision.canton,
                legal_provision.municipality,
                article_nrs,
                legal_provision.file,
                article_records,
                referenced_document_records
            ))
        return document_records

    def from_db_to_plr_record(self, public_law_restriction_from_db):
        thresholds = self._plr_info.get('thresholds')
        min_length = thresholds.get('length').get('limit')
        length_unit = thresholds.get('length').get('unit')
        length_precision = thresholds.get('length').get('precision')
        min_area = thresholds.get('area').get('limit')
        area_unit = thresholds.get('area').get('unit')
        area_precision = thresholds.get('area').get('precision')
        percentage_precision = thresholds.get('percentage').get('precision')
        legend_entry_records = self.from_db_to_legend_entry_record(
            self._theme_record,
            public_law_restriction_from_db.view_service.legends
        )
        symbol = None
        for legend_entry_record in legend_entry_records:
            if public_law_restriction_from_db.type_code == legend_entry_record.type_code:
                symbol = legend_entry_record.symbol
        if symbol is None:
            # TODO: raise real error here when data is correct, emit warning for now
            msg = u'No symbol was found for plr in topic {topic} with id {id}'.format(
                topic=self._plr_info.get('code'),
                id=public_law_restriction_from_db.id
            )
            log.warning(msg)
            symbol = ImageRecord('1'.encode('utf-8'))
            # raise AttributeError(msg)
        view_service_record = self.from_db_to_view_service_record(
            public_law_restriction_from_db.view_service,
            legend_entry_records
        )
        documents_from_db = []
        article_numbers = []
        for i, legal_provision in enumerate(public_law_restriction_from_db.legal_provisions):
            documents_from_db.append(legal_provision.document)
            article_nrs = legal_provision.article_numbers.split('|') if legal_provision.article_numbers \
                else None
            article_numbers.append(article_nrs)
        document_records = self.from_db_to_document_records(documents_from_db, article_numbers)
        geometry_records = self.from_db_to_geometry_records(public_law_restriction_from_db.geometries)

        basis_plr_records = []
        for join in public_law_restriction_from_db.basis:
            basis_plr_records.append(self.from_db_to_plr_record(join.base))
        refinements_plr_records = []
        for join in public_law_restriction_from_db.refinements:
            refinements_plr_records.append(self.from_db_to_plr_record(join.refinement))
        law_status = LawStatusRecord.from_config(
            Config.get_law_status(
                self._plr_info.get('code'),
                self._plr_info.get('law_status'),
                public_law_restriction_from_db.law_status
            )
        )

        plr_record = self._plr_record_class(
            self._theme_record,
            public_law_restriction_from_db.information,
            law_status,
            public_law_restriction_from_db.published_from,
            self.from_db_to_office_record(public_law_restriction_from_db.responsible_office),
            symbol,
            view_service_record,
            geometry_records,
            sub_theme=public_law_restriction_from_db.sub_theme,
            other_theme=public_law_restriction_from_db.other_theme,
            type_code=public_law_restriction_from_db.type_code,
            type_code_list=public_law_restriction_from_db.type_code_list,
            basis=basis_plr_records,
            refinements=refinements_plr_records,
            documents=document_records,
            min_area=min_area,
            min_length=min_length,
            area_unit=area_unit,
            length_unit=length_unit,
            area_precision=area_precision,
            length_precision=length_precision,
            percentage_precision=percentage_precision
        )

        return plr_record

    @staticmethod
    def extract_geometry_collection_db(db_path, real_estate_geometry):
        """
        Decides the geometry collection cases of geometric filter operations when the database contains multi
        geometries but the passed geometry does not.
        The multi geometry will be extracted to it's sub parts for operation.

        Args:
            db_path (str): The point separated string of schema_name.table_name.column_name from
                which we can construct a correct SQL statement.
            real_estate_geometry (shapely.geometry.base.BaseGeometry): The shapely geometry
                representation which is used for comparison.

        Returns:
            sqlalchemy.sql.elements.BooleanClauseList: The clause element.

        Raises:
            HTTPBadRequest
        """
        srid = Config.get('srid')
        sql_text_point = 'ST_Intersects(ST_CollectionExtract({0}, 1), ST_GeomFromText(\'{1}\', {2}))'.format(
            db_path,
            real_estate_geometry.wkt,
            srid
        )
        sql_text_line = 'ST_Intersects(ST_CollectionExtract({0}, 2), ST_GeomFromText(\'{1}\', {2}))'.format(
            db_path,
            real_estate_geometry.wkt,
            srid
        )
        sql_text_polygon = \
            'ST_Intersects(ST_Envelope({0}), ' \
            'ST_GeomFromText(\'{1}\', {2}))'.format(
                db_path,
                real_estate_geometry.wkt,
                srid
            )
        clause_blocks = [
            text(sql_text_point),
            text(sql_text_line),
            text(sql_text_polygon)
        ]
        return or_(*clause_blocks)

    def handle_collection(self, session, geometry_to_check):
        geometry_types = Config.get('geometry_types')
        collection_types = geometry_types.get('collection').get('types')
        # Check for Geometry type, cause we can't handle geometry collections the same as specific geometries
        if self._plr_info.get('geometry_type') in [x.upper() for x in collection_types]:

            # The PLR is defined as a collection type. We need to do a special handling
            query = session.query(self._model_).filter(
                self.extract_geometry_collection_db(
                    '{schema}.{table}.geom'.format(
                        schema=self._model_.__table__.schema,
                        table=self._model_.__table__.name
                    ),
                    geometry_to_check
                )
            )

        else:
            # The PLR is not problematic at all cause we do not have a collection type here
            query = session.query(self._model_).filter(self._model_.geom.ST_Intersects(
                from_shape(geometry_to_check, srid=Config.get('srid'))
            ))
        return query

    def collect_related_geometries_by_real_estate(self, session, real_estate):
        """
        Extracts all geometries in the topic which have spatial relation with the passed real estate

        Args:
            session (sqlalchemy.orm.Session): The requested clean session instance ready for use
            real_estate (pyramid_oereb.lib.records.real_estate.RealEstateRecord): The real
                estate in its record representation.

        Returns:
            list: The result of the related geometries unique by the public law restriction id
        """
        return self.handle_collection(session, real_estate.limit).distinct(
            self._model_.public_law_restriction_id
        ).all()

    def read(self, real_estate, bbox):
        """
        The read point which creates a extract, depending on a passed real estate.

        Args:
            real_estate (pyramid_oereb.lib.records.real_estate.RealEstateRecord): The real
                estate in its record representation.
            bbox (shapely.geometry.base.BaseGeometry): The bbox to search the records.
        """
        log.debug("read() start")

        # Check if the plr is marked as available
        if self._is_available(real_estate):
            session = self._adapter_.get_session(self._key_)
            try:
                if session.query(self._model_).count() == 0:
                    # We can stop here already because there are no items in the database
                    self.records = [EmptyPlrRecord(self._theme_record)]
                else:
                    # We need to investigate more in detail

                    # Try to find geometries which have spatial relation with real estate
                    geometry_results = self.collect_related_geometries_by_real_estate(
                        session, real_estate
                    )
                    if len(geometry_results) == 0:
                        # We checked if there are spatially related elements in database. But there is none.
                        # So we can stop here.
                        self.records = [EmptyPlrRecord(self._theme_record)]
                    else:
                        # We found spatially related elements. This means we need to extract the actual plr
                        # information related to the found geometries.
                        self.records = []
                        for geometry_result in geometry_results:
                            self.records.append(
                                self.from_db_to_plr_record(geometry_result.public_law_restriction)
                            )
                        log.debug("read() processed {} geometry_results into {} plr".format(
                            len(geometry_results), len(self.records))
                        )

            finally:
                session.close()

        # Add empty record if topic is not available
        else:
            self.records = [EmptyPlrRecord(self._theme_record, has_data=False)]

    def _is_available(self, real_estate):
        """
        Checks if the topic is available for the specified real estate.

        Args:
            real_estate (pyramid_oereb.lib.records.real_estate.RealEstateRecord): The real
                estate in its record representation.

        Returns:
             bool: True if the topic is available, false otherwise.
        """
        for availability in self.availabilities:
            if real_estate.fosnr == availability.fosnr and not availability.available:
                return False
        return True
