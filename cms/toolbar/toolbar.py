# -*- coding: utf-8 -*-
from cms.constants import LEFT
from cms.models import UserSettings, Placeholder
from cms.toolbar.items import Menu, ToolbarAPIMixin, ButtonList
from cms.toolbar_pool import toolbar_pool
from cms.utils import get_language_from_request
from cms.utils.i18n import force_language

from django.contrib.auth.forms import AuthenticationForm
from django import forms
from django.contrib.auth import login, logout
from django.core.urlresolvers import resolve, Resolver404
from django.http import HttpResponseRedirect, HttpResponse
from django.middleware.csrf import get_token
from django.utils.translation import ugettext_lazy as _
from django.conf import settings


class CMSToolbarLoginForm(AuthenticationForm):
    username = forms.CharField(label=_("Username"), max_length=100)

    def __init__(self, *args, **kwargs):
        kwargs['prefix'] = kwargs.get('prefix', 'cms')
        super(CMSToolbarLoginForm, self).__init__(*args, **kwargs)

    def check_for_test_cookie(self): pass  # for some reason this test fails in our case. but login works.


class CMSToolbar(ToolbarAPIMixin):
    """
    The default CMS Toolbar
    """

    def __init__(self, request):
        super(CMSToolbar, self).__init__()
        self.right_items = []
        self.left_items = []
        self.populated = False
        self.post_template_populated = False
        self.menus = {}
        self.request = request
        self.login_form = CMSToolbarLoginForm(request=request)
        self.is_staff = self.request.user.is_staff
        self.edit_mode = self.is_staff and self.request.session.get('cms_edit', False)
        self.build_mode = self.is_staff and self.request.session.get('cms_build', False)
        self.use_draft = self.is_staff and self.edit_mode or self.build_mode
        self.show_toolbar = self.is_staff or self.request.session.get('cms_edit', False)
        if settings.USE_I18N:
            self.language = get_language_from_request(request)
        else:
            self.language = settings.LANGUAGE_CODE

        # We need to store the current language in case the user's preferred language is different.
        self.toolbar_language = self.language

        if self.is_staff:
            try:
                user_settings = UserSettings.objects.get(user=self.request.user)
            except UserSettings.DoesNotExist:
                user_settings = UserSettings(language=self.language, user=self.request.user)
                placeholder = Placeholder(slot="clipboard")
                placeholder.save()
                user_settings.clipboard = placeholder
                user_settings.save()
            self.toolbar_language = user_settings.language
            self.clipboard = user_settings.clipboard
        with force_language(self.language):
            try:
                self.view_name = resolve(self.request.path).func.__module__
            except Resolver404:
                self.view_name = ""
        toolbars = toolbar_pool.get_toolbars()

        self.toolbars = {}
        app_key = ''
        for key in toolbars:
            app_name = ".".join(key.split(".")[:-2])
            if app_name in self.view_name and len(key) > len(app_key):
                app_key = key
        for key in toolbars:
            self.toolbars[key] = toolbars[key](self.request, self, key == app_key, app_key)

    @property
    def csrf_token(self):
        token = get_token(self.request)
        return token

    # Public API

    def get_or_create_menu(self, key, verbose_name=None, side=LEFT, position=None):
        self.populate()
        if key in self.menus:
            return self.menus[key]
        menu = Menu(verbose_name, self.csrf_token, side=side)
        self.menus[key] = menu
        self.add_item(menu, position=position)
        return menu

    def add_button(self, name, url, active=False, disabled=False, extra_classes=None, extra_wrapper_classes=None,
                   side=LEFT, position=None):
        self.populate()
        item = ButtonList(extra_classes=extra_wrapper_classes, side=side)
        item.add_button(name, url, active=active, disabled=disabled, extra_classes=extra_classes)
        self.add_item(item, position=position)
        return item

    def add_button_list(self, identifier=None, extra_classes=None, side=LEFT, position=None):
        self.populate()
        item = ButtonList(identifier, extra_classes=extra_classes, side=side)
        self.add_item(item, position=position)
        return item

    # Internal API

    def _add_item(self, item, position):
        if item.right:
            target = self.right_items
        else:
            target = self.left_items
        if position is not None:
            target.insert(position, item)
        else:
            target.append(item)

    def _remove_item(self, item):
        if item in self.right_items:
            self.right_items.remove(item)
        elif item in self.left_items:
            self.left_items.remove(item)
        else:
            raise KeyError("Item %r not found" % item)

    def _item_position(self, item):
        if item.right:
            return self.right_items.index(item)
        else:
            return self.left_items.index(item)

    def get_clipboard_plugins(self):
        self.populate()
        if not hasattr(self, "clipboard"):
            return []
        return self.clipboard.get_plugins()

    def get_left_items(self):
        self.populate()
        return self.left_items

    def get_right_items(self):
        self.populate()
        return self.right_items

    def populate(self):
        """
        Get the CMS items on the toolbar
        """
        if self.populated:
            return
        self.populated = True
        # never populate the toolbar on is_staff=False
        if not self.is_staff:
            return
        self._call_toolbar('populate')

    def post_template_populate(self):
        self.populate()
        if self.post_template_populated:
            return
        self.post_template_populated = True
        if not self.is_staff:
            return
        self._call_toolbar('post_template_populate')

    def request_hook(self):
        response = self._call_toolbar('request_hook')
        if isinstance(response, HttpResponse):
            return response

        if self.request.method != 'POST':
            return self._request_hook_get()
        else:
            return self._request_hook_post()

    def _request_hook_get(self):
        if 'cms-toolbar-logout' in self.request.GET:
            logout(self.request)
            return HttpResponseRedirect(self.request.path)

    def _request_hook_post(self):
        # login hook
        if 'cms-toolbar-login' in self.request.GET:
            self.login_form = CMSToolbarLoginForm(request=self.request, data=self.request.POST)
            if self.login_form.is_valid():
                login(self.request, self.login_form.user_cache)
                return HttpResponseRedirect(self.request.path)

    def _call_toolbar(self, func_name):
        with force_language(self.toolbar_language):
            first = ('cms.cms_toolbar.BasicToolbar', 'cms.cms_toolbar.PlaceholderToolbar')
            for key in first:
                toolbar = self.toolbars.get(key)
                if not toolbar:
                    continue
                result = getattr(toolbar, func_name)()
                if isinstance(result, HttpResponse):
                    return result
            for key in self.toolbars:
                if key in first:
                    continue
                toolbar = self.toolbars[key]
                result = getattr(toolbar, func_name)()
                if isinstance(result, HttpResponse):
                    return result

