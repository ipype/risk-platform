# Import Base and every model here so Alembic autogenerate can see the full metadata.
from app.db.base_class import Base  # noqa: F401
from app.models.system import SystemMeta  # noqa: F401
from app.models.rbs import RbsCategory, RbsSubcategory  # noqa: F401