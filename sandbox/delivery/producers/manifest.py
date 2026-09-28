"""Fixed producer registry, with no runtime extension mechanism."""
from .lenzora import decode_owner_projection
from ..trace_models import STAGES

PRODUCERS = ({'producer_id':'lenzora-hosted-v1','producer_version':1,
              'projection_schema':1,'supported_stages':STAGES,
              'decoder':decode_owner_projection},)

def descriptors():
    return [{key:list(value) if key=='supported_stages' else value
             for key,value in producer.items() if key!='decoder'} for producer in PRODUCERS]

def get_producer(producer_id):
    return next((item for item in PRODUCERS if item['producer_id']==producer_id),None)
