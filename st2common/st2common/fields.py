# Copyright 2020 The StackStorm Authors.
# Copyright 2019 Extreme Networks, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import

import datetime
import calendar

import six

from mongoengine import LongField
from mongoengine import BinaryField

from st2common.util import date as date_utils
from st2common.util import mongoescape

__all__ = [
    'ComplexDateTimeField'
]

SECOND_TO_MICROSECONDS = 1000000


class ComplexDateTimeField(LongField):
    """
    Date time field which handles microseconds exactly and internally stores
    the timestamp as number of microseconds since the unix epoch.

    Note: We need to do that because mongoengine serializes this field as comma
    delimited string which breaks sorting.
    """

    def _convert_from_datetime(self, val):
        """
        Convert a `datetime` object to number of microseconds since epoch representation
        (which will be stored in MongoDB). This is the reverse function of
        `_convert_from_db`.
        """
        result = self._datetime_to_microseconds_since_epoch(value=val)
        return result

    def _convert_from_db(self, value):
        result = self._microseconds_since_epoch_to_datetime(data=value)
        return result

    def _microseconds_since_epoch_to_datetime(self, data):
        """
        Convert a number representation to a `datetime` object (the object you
        will manipulate). This is the reverse function of
        `_convert_from_datetime`.

        :param data: Number of microseconds since the epoch.
        :type data: ``int``
        """
        result = datetime.datetime.utcfromtimestamp(data // SECOND_TO_MICROSECONDS)
        microseconds_reminder = (data % SECOND_TO_MICROSECONDS)
        result = result.replace(microsecond=microseconds_reminder)
        result = date_utils.add_utc_tz(result)
        return result

    def _datetime_to_microseconds_since_epoch(self, value):
        """
        Convert datetime in UTC to number of microseconds from epoch.

        Note: datetime which is passed to the function needs to be in UTC timezone (e.g. as returned
        by ``datetime.datetime.utcnow``).

        :rtype: ``int``
        """
        # Verify that the value which is passed in contains UTC timezone
        # information.
        if not value.tzinfo or (value.tzinfo.utcoffset(value) != datetime.timedelta(0)):
            raise ValueError('Value passed to this function needs to be in UTC timezone')

        seconds = calendar.timegm(value.timetuple())
        microseconds_reminder = value.time().microsecond
        result = (int(seconds * SECOND_TO_MICROSECONDS) + microseconds_reminder)
        return result

    def __get__(self, instance, owner):
        data = super(ComplexDateTimeField, self).__get__(instance, owner)
        if data is None:
            return None
        if isinstance(data, datetime.datetime):
            return data
        return self._convert_from_db(data)

    def __set__(self, instance, value):
        value = self._convert_from_datetime(value) if value else value
        return super(ComplexDateTimeField, self).__set__(instance, value)

    def validate(self, value):
        value = self.to_python(value)
        if not isinstance(value, datetime.datetime):
            self.error('Only datetime objects may used in a '
                       'ComplexDateTimeField')

    def to_python(self, value):
        original_value = value
        try:
            return self._convert_from_db(value)
        except:
            return original_value

    def to_mongo(self, value):
        value = self.to_python(value)
        return self._convert_from_datetime(value)

    def prepare_query_value(self, op, value):
        return self._convert_from_datetime(value)



class JSONDictField(BinaryField):
    """
    Custom field types which stores dictionary as JSON serialized strings.

    This is done because storing large objects as JSON serialized strings is much more efficent
    on the serialize and unserialize paths compared to used EscapedDictField which needs to escape
    all the special values ($, .).

    Only downside is that to MongoDB those values are plain raw strings which means you can't query
    on actual dictionary field values. That's not an issue for us, because in places where we use
    it, we already treat those values more or less as opaque strings.

    # NOTE(Tomaz): I've done bencharmking of ujson and cjson and cjson is more performant on large
    objects and ujson on smaller ones.
    """
    def __init__(self, *args, **kwargs):
        # TODO: Based on the benchmark results we should support just a single backend
        json_backend = kwargs.pop('json_backend', 'orjson')
        compression_algorithm = kwargs.pop('compression_algorithm', 'none')

        if json_backend not in ['orjson', 'ujson', 'cjson']:
            raise ValueError('Unsupported backend: %s' % (json_backend))

        super(JSONDictField, self).__init__(*args, **kwargs)

        if json_backend == "orjson":
            import orjson

            self.json_loads = orjson.loads
            self.json_dumps = orjson.dumps
        elif json_backend == 'ujson':
            import ujson

            self.json_loads = ujson.loads
            self.json_dumps = ujson.dumps
        elif json_backend == 'cjson':
            import cjson
            self.json_loads = cjson.decode
            self.json_dumps = cjson.encode

    def to_mongo(self, value):
        # TODO: Use this format: <json lib>:<compression string>:<data> for better forward
        # compatibility if case we ever want to change the JSON lib or compression algorithm
        if not isinstance(value, dict):
            raise ValueError('value argument must be a dictionary')

        data = self.json_dumps(value)
        return data

    def to_python(self, value):
        if isinstance(value, (six.text_type, six.binary_type)):
            return self.json_loads(value)

        return value

    def validate(self, value):
        value = self.to_mongo(value)
        return super(JSONDictField, self).validate(value)


class JSONDictEscapedFieldCompatibilityField(JSONDictField):
    """
    Special version of JSONDictField which takes care of compatibility between old EscapedDictField
    and EscapedDynamicField format and the new one.

    On retrieval, if an old format is detected it's correctly un-serialized and on insertion, we
    always insert data in a new format.
    """

    def to_mongo(self, value):
        if not isinstance(value, dict):
            raise ValueError('value argument must be a dictionary')

        return self.json_dumps(value)

    def to_python(self, value):
        if isinstance(value, dict):
            # Old format which used a native dict with escaped special characters
            value = mongoescape.unescape_chars(value)
            return value

        if isinstance(value, (six.text_type, six.binary_type)):
            return self.json_loads(value)

        return value
