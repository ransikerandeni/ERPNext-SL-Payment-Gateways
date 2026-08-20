"""A minimal stand-in for the parts of `frappe` this app actually uses.

These are pure unit tests of cryptography and input validation - none of
it touches the database - so stubbing the framework lets the whole suite
run with `pytest` and no bench, site or MySQL. Installed into
sys.modules by conftest.py before the app is imported.

Only what the app calls is implemented, on purpose: if a future change
starts using some other part of frappe, the tests fail loudly here rather
than quietly exercising a mock that agrees with everything.
"""

import sys
import types


class ValidationError(Exception):
	pass


class PermissionError_(Exception):  # named so it doesn't shadow the builtin here
	pass


class DoesNotExistError(Exception):
	pass


class _dict(dict):
	"""frappe._dict: attribute access, and update() returns self."""

	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			return None

	def __setattr__(self, key, value):
		self[key] = value

	def update(self, *args, **kwargs):
		super().update(*args, **kwargs)
		return self

	def copy(self):
		return _dict(self)


class FakeSettingsDoc(_dict):
	"""Stands in for a Single doctype with Password fields."""

	def __init__(self, values, passwords=None):
		super().__init__(values)
		# dict.__setattr__ would land in the dict itself, so go around it.
		object.__setattr__(self, "_passwords", passwords or {})

	def get_password(self, fieldname, raise_exception=True):
		value = object.__getattribute__(self, "_passwords").get(fieldname)
		if not value and raise_exception:
			raise ValidationError("Password not found for %s" % (fieldname,))
		return value


def build_module(site_url="https://erp.example.com"):
	"""Create a fresh fake `frappe` module.

	Returns the module; tests drive it through `frappe.test_docs`
	(doctype name -> FakeSettingsDoc) and `frappe.local.form_dict`.
	"""
	frappe = types.ModuleType("frappe")

	frappe.ValidationError = ValidationError
	frappe.PermissionError = PermissionError_
	frappe.DoesNotExistError = DoesNotExistError
	frappe._dict = _dict

	frappe.test_docs = {}
	frappe.local = types.SimpleNamespace(form_dict=_dict())

	def throw(msg, exc=ValidationError):
		raise exc(msg)

	def get_doc(doctype, *args, **kwargs):
		if doctype not in frappe.test_docs:
			raise DoesNotExistError(doctype)
		doc = frappe.test_docs[doctype]
		doc.setdefault("doctype", doctype)
		return doc

	def whitelist(allow_guest=False, methods=None):
		def decorator(fn):
			fn.whitelisted = True
			fn.allow_guest = allow_guest
			return fn

		return decorator

	frappe.throw = throw
	frappe.get_doc = get_doc
	frappe.whitelist = whitelist

	# frappe.form_dict is a proxy onto frappe.local in the real thing;
	# a property on the module type is the closest simple equivalent.
	class _FrappeModule(types.ModuleType):
		@property
		def form_dict(self):
			return frappe.local.form_dict

	frappe.__class__ = _FrappeModule

	utils = types.ModuleType("frappe.utils")

	def get_url(path=""):
		if not path:
			return site_url
		return site_url.rstrip("/") + "/" + str(path).lstrip("/")

	def escape_html(text):
		return (
			str(text)
			.replace("&", "&amp;")
			.replace("<", "&lt;")
			.replace(">", "&gt;")
			.replace('"', "&quot;")
			.replace("'", "&#39;")
		)

	utils.get_url = get_url
	utils.escape_html = escape_html
	frappe.utils = utils

	# frappe.model.document.Document - just enough for the Settings
	# doctype controllers to be importable. They are plain classes with
	# hook methods on them; nothing here needs a real Document.
	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")

	class Document:
		def __init__(self, **kwargs):
			self.__dict__.update(kwargs)

		def get_password(self, fieldname, raise_exception=True):
			return self.__dict__.get(fieldname)

	document.Document = Document
	model.document = document
	frappe.model = model

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document

	return frappe
