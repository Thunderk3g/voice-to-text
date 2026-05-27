"""
GET /cluster/{id} — full cluster detail for the Cluster Explorer view.

Delegates the heavy lifting (centroid + distribution stats) to
:class:`ClusterEngine.cluster_detail` so the same logic is reusable in
worker code paths.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_cluster_engine
from app.api.errors import APIError
from app.core.logging import get_logger
from app.models.schemas import ClusterDetail

logger = get_logger(__name__)
router = APIRouter(tags=["clusters"])


@router.get("/cluster/{cluster_id}", response_model=ClusterDetail)
async def get_cluster(
    cluster_id: UUID,
    engine=Depends(get_cluster_engine),
) -> ClusterDetail:
    try:
        detail = await engine.cluster_detail(cluster_id)
    except LookupError as exc:
        raise APIError(
            f"Cluster {cluster_id} not found.",
            status_code=status.HTTP_404_NOT_FOUND,
            error_type="cluster_not_found",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("cluster_detail_failed", cluster_id=str(cluster_id), error=str(exc))
        raise APIError(
            "Failed to fetch cluster detail.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_type="cluster_detail_failed",
        ) from exc
    return detail
