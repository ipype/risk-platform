# Import Base and every model here so Alembic autogenerate can see the full metadata.
from app.db.base_class import Base  # noqa: F401
from app.models.custom_fields import CustomFieldConfig  # noqa: F401
from app.models.history import RiskHistory  # noqa: F401
from app.models.matrix import MatrixConfig  # noqa: F401
from app.models.mitigation import (  # noqa: F401
    MitigationAction,
    MitigationPlan,
    MitigationPlanRisk,
)
from app.models.rbs import RbsCategory, RbsSubcategory  # noqa: F401
from app.models.roi import MitigationRoi  # noqa: F401
from app.models.risk import Risk  # noqa: F401
from app.models.scope import ScopeNode  # noqa: F401
from app.models.schedule import (  # noqa: F401
    DcmaRun,
    ScheduleActivity,
    ScheduleCalendar,
    ScheduleFile,
    ScheduleRelationship,
    ScheduleVersion,
    ScheduleWbs,
)
from app.models.simulation import SimulationRun  # noqa: F401
from app.models.system import SystemMeta  # noqa: F401

from app.models.mapping import (  # noqa: F401
    MappingHistory,
    MappingSuggestionOutcome,
    RiskActivityMapping,
)
from app.models.quant import (  # noqa: F401
    RiskDriver,
    RiskDriverLink,
    RiskQuantEstimate,
)
