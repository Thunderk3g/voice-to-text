# app/tests/unit/test_crosswalk.py
from app.services.enrichment.crosswalk import load_crosswalk, map_lms_disposition
from app.models.enums import CallDisposition


def test_crosswalk_maps_known_lms_values():
    assert map_lms_disposition("Ringing") == CallDisposition.NO_RESPONSE
    assert map_lms_disposition("NW - DNC") == CallDisposition.DND
    assert map_lms_disposition("NI - Due to Price") == CallDisposition.NOT_INTERESTED
    assert map_lms_disposition("Meeting Scheduled") == CallDisposition.OTHER

def test_unknown_maps_to_other():
    assert map_lms_disposition("totally novel value") == CallDisposition.OTHER

def test_none_maps_to_other():
    assert map_lms_disposition(None) == CallDisposition.OTHER

def test_every_target_is_a_valid_enum():
    targets = set(load_crosswalk().values())
    assert targets <= {d.value for d in CallDisposition}
