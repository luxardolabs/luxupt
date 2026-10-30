"""Each row of the users list opens that user's edit form and delete confirmation.

The row's buttons passed their URLs to `icon_button` as Jinja string literals holding
`{{ u.id }}`. A string literal is not a template, so the button rendered the braces verbatim and
every Edit and Delete request asked for `user_id={{ u.id }}`. The view now resolves each row's
URLs by route name.
"""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.user_crud import user_crud
from app.web.main import app


async def test_each_user_row_targets_that_users_form_and_delete_panel(
    auth_client: AsyncClient, db: AsyncSession
) -> None:
    other = await user_crud.create_user(
        db, username="second-user", password="a-long-enough-password"
    )

    resp = await auth_client.get(app.url_path_for("system.users_list_partial"))

    assert resp.status_code == 200
    html = resp.text
    assert "{{" not in html
    edit = app.url_path_for("system.user_form_panel")
    delete = app.url_path_for("system.user_delete_confirm_panel", user_id=str(other.id))
    assert f'hx-get="{edit}?user_id={other.id}"' in html
    assert f'hx-get="{delete}"' in html
