# imports # {{{ 
from django.utils import simplejson
from django.views.generic.simple import direct_to_template
from django.contrib import admin
from django.conf.urls.defaults import url
from django.conf import settings
from django import forms
from django.core.files.base import ContentFile
from django.utils.encoding import smart_str, smart_unicode
from django.http import HttpResponseServerError, HttpResponseRedirect
from django.template import loader, RequestContext
from django.utils.translation import force_unicode
from django.http import HttpResponse, Http404
from django.core.urlresolvers import get_mod_func
from django.template.defaultfilters import filesizeformat
from django.core.paginator import Paginator, InvalidPage
from django.utils.functional import Promise
from django.db.models.query import QuerySet
from django.contrib.auth.models import User, SiteProfileNotAvailable
from django.db import models
from django.core.urlresolvers import get_urlconf, get_resolver, Resolver404
from django.core import mail

import time, random, re, os, sys, traceback, socket
from hashlib import md5
import urllib2, urllib, threading, cgi, itertools
from PIL import Image
from functools import wraps
from datetime import datetime, date

import logging
import cStringIO
try:
    import solr
except ImportError: 
    pass
# }}} 

# threaded_task # {{{ 
def threaded_task(func):
    def decorated(*args, **kwargs):
        thread = threading.Thread(target=func, args=args, kwargs=kwargs)
        thread.start()
        return thread
    decorated.__doc__ = func.__doc__
    decorated.__name__ = func.__name__
    return decorated
# }}} 

# logging # {{{
def create_logger(name=None, level=logging.DEBUG):
    if name is None:
        name = settings.APP_DIR.namebase
    logger = logging.getLogger(name)
    hdlr = logging.FileHandler(
        settings.APP_DIR.joinpath("%s.log" % name)
    )
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    hdlr.setFormatter(formatter)
    logger.addHandler(hdlr)
    logger.setLevel(level)
    return logger

if hasattr(settings, "APP_DIR"): logger = create_logger()

class PrintLogger(object): 
    def __init__(self, old_out):
        self.old_out = old_out

    def write(self, astring): 
        logger.debug(astring)
        self.old_out.write(astring)

#sys.stdout = PrintLogger(sys.stdout)
# }}}

# SimpleExceptionHandler www.djangosnippets.org/snippets/650/ # {{{
class SimpleExceptionHandler:
    def process_exception(self, request, exception):
        (exc_type, exc_info, tb) = sys.exc_info()
        response = "%s\n" % getattr(exc_type, "__name__", exc_type)
        response += "%s\n\n" % exc_info
        response += "TRACEBACK:\n"    
        for tb in traceback.format_tb(tb):
            response += "%s\n" % tb
        logger.exception(exception)    
        logger.info(request.POST)
        logger.info(request.GET)
        logger.info(request.META)
        logger.info(request.COOKIES)
        if not settings.DEBUG: return
        if not request.is_ajax(): return
        return HttpResponseServerError(response)
# }}}

# uuid # {{{ 
def uuid( *args ):
  """
    Generates a universally unique ID.
    Any arguments only create more randomness.
  """
  t = long( time.time() * 1000 )
  r = long( random.random()*100000000000000000L )
  try:
    a = socket.gethostbyname( socket.gethostname() )
  except:
    # if we can't get a network address, just imagine one
    a = random.random()*100000000000000000L
  data = str(t)+' '+str(r)+' '+str(a)+' '+str(args)
  data = md5(data).hexdigest()
  return data
# }}}  

# solr related functions # {{{ 
def solr_add(**data_dict):
    s = solr.SolrConnection(settings.SOLR_ROOT)
    s.add(**data_dict)
    s.commit()
    s.close()

def solr_delete(id):
    s = solr.SolrConnection(settings.SOLR_ROOT)
    s.delete(id)
    s.commit()
    s.close()

def solr_search(
    q, fields=None, highlight=None, score=True, 
    sort=None, sort_order="asc", **params
):
    s = solr.SolrConnection(settings.SOLR_ROOT)
    response = s.query(
        q, fields, highlight, score, sort, sort_order, **params
    )
    return response

def solr_paginator(q, start,rows):
    response = {}
    conn = solr.SolrConnection(settings.SOLR_ROOT)
    res = conn.query(q)
    numFound = int(res.results.numFound)
    results = res.next_batch(start=start,rows=rows).results
    response['results'] = [dict(element) for element in results]
    response['count'] = numFound
    response['num_found'] = len(response['results'])
    response['has_prev'] = True
    response['has_next'] = True
    if start <= 0:
        response['has_prev'] = False
    if (start + rows) >= numFound:
        response['has_next'] = False
    return response
# }}}

# solr_time # {{{
def solr_time(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
# }}}

# request context preprocessor # {{{ 
def context_preprocessor(request):
    d = {}
    d["path"] = request.path
    return d
# }}}

# RequestForm # {{{
class RequestForm(forms.Form):
    def __init__(self, request, *args, **kw):
        super(RequestForm, self).__init__(*args, **kw)
        self.request = request

    def get_json(self, saved):
        if hasattr(self, "obj"):
            if hasattr(self.obj, "get_json"):
                return self.obj.get_json()
            return self.obj
        if hasattr(saved, "get_json"):
            return saved.get_json()
        return saved

    def initialize(self, field=None, value=None, **kw):
        if field: self.fields[field].initial = value
        for k, v in kw.items():
            self.fields[k].initial = v
        return self

    def initialize_with_object(self, obj, *fields, **kw):
        for field in fields:
            self.fields[field].initial = getattr(obj, field)
        for ffield,ofield in kw.items():
            self.fields[ffield].initial = getattr(obj, ofield)
        return self

    def update_object(self, obj, *args, **kw):
        d = self.cleaned_data.get
        for arg in args:
            setattr(obj, arg, d(arg))
        for k, v in kw.items():
            setattr(obj, k, d(v))
        return obj
# }}}

# profane words # {{{ 
class SacredField(forms.CharField):
    def clean(self, value):
        value = super(SacredField,self).clean(value)
        value_words = re.split('\W+', value)
        for word in settings.PROFANE_WORDS.split(","):
            for val_word in value_words:
                if(val_word == word):
                    raise forms.ValidationError("%s is not an allowed word." % val_word)
        return value

class SacredANField(forms.CharField):
    def clean(self, value):
        value = super(SacredField,self).clean(value)
        value_words = re.split('\W+', value)
        for word in settings.PROFANE_WORDS.split(","):
            for val_word in value_words:
                if(val_word == word):
                    raise forms.ValidationError("%s is not an allowed word." % val_word)
        pattern = "^[a-zA-Z\s]+$"
        match = re.match(pattern, value)
        if not match:
            raise forms.ValidationError("Special characters not allowed.")
        return value.strip()
# }}} 

# resize_image # {{{
def resize_image(image, thumb_size, square, format):
    #img.seek(0) # see http://code.djangoproject.com/ticket/8222 for details
    #image = Image.open(img)
    
    # Convert to RGB if necessary
    if image.mode not in ('L', 'RGB'):
        image = image.convert('RGB')
        
    # get size
    thumb_w, thumb_h = thumb_size
    # If you want to generate a square thumbnail
    #if thumb_w == thumb_h:
    if square:
        # quad
        xsize, ysize = image.size
        # get minimum size
        minsize = min(xsize,ysize)
        # largest square possible in the image
        xnewsize = (xsize-minsize)/2
        ynewsize = (ysize-minsize)/2
        # crop it
        image2 = image.crop(
            (xnewsize, ynewsize, xsize-xnewsize, ysize-ynewsize)
        )
        # load is necessary after crop                
        image2.load()
        # thumbnail of the cropped image (ANTIALIAS to make it look better)
        image2.thumbnail(thumb_size, Image.ANTIALIAS)
    else:
        # not quad
        image2 = image
        image2.thumbnail(thumb_size, Image.ANTIALIAS)
    
    io = cStringIO.StringIO()
    # PNG and GIF are the same, JPG is JPEG
    if format.upper()=='JPG':
        format = 'JPEG'
    
    image2.save(io, format)
    return Image.open(ContentFile(io.getvalue()))    
# }}}

# crop_imgae # {{{
def crop_image(img, x, y, w, h):
    #image.seek(0)
    #img = Image.open(image)
    box = (x, y, x+w, y+h)
    region = img.crop(box)
    io = cStringIO.StringIO()
    region.save(io, img.format)
    return Image.open(ContentFile(io.getvalue()))
# }}}

# ext_add # {{{
def ext_add(value,add):
    p = os.path.splitext(value)
    return p[0] + add + p[1]
# }}}

# process_image # {{{
def process_image(image_name, photo, x, y, w, h, size=(58,72)):
    cropped_image = crop_image(photo,x,y,w,h)
    final_image = resize_image(cropped_image,size, False, 'JPEG')
    return update_jpg(img=final_image, key=image_name)
# }}}

# clear_unicode # {{{ 
def clear_unicode(object):
    if type(object) == type({}):
        return dict(
            [(str(k), v) for k, v in object.items()]
        )
    else:
        return object
# }}} 

# formatExceptionInfo # {{{ 
def formatExceptionInfo(level = 6):
    error_type, error_value, trbk = sys.exc_info()
    tb_list = traceback.format_tb(trbk, level)   
    s = "Error: %s \nDescription: %s \nTraceback:" % (
        getattr(error_type, "__name__", error_type), error_value
    )
    for i in tb_list:
        s += "\n" + i
    return s
# }}} 

format_exception = formatExceptionInfo

# S3 Photo Storeage # {{{
def delete_jpg(key):
    import boto
    if settings.USE_S3_BACKEND:
        conn = boto.connect_s3(
            settings.S3_ACCESS_KEY, settings.S3_SECRET_KEY
        )
        bucket_1 = conn.create_bucket(settings.S3_BUCKET_1)
        bucket_2 = conn.create_bucket(settings.S3_BUCKET_2)
        # delete old key
        for k in itertools.chain(
            bucket_1.get_all_keys(prefix=key + "/"), 
            bucket_2.get_all_keys(prefix=key + "/"),
        ): k.bucket.delete_key(k.key)
    else: pass # we dont care about local storage cleanup. Lazy me.

def update_gif(key, data):
    import boto
    from boto.s3.key import Key
    salt = random.randint(0, 1000)
    full_key = "%s/%s.gif" % (key, salt)
    if settings.USE_S3_BACKEND:
        conn = boto.connect_s3(
            settings.S3_ACCESS_KEY, settings.S3_SECRET_KEY
        )
        bucket_1 = conn.create_bucket(settings.S3_BUCKET_1)
        bucket_2 = conn.create_bucket(settings.S3_BUCKET_2)
        # create new data
        bucket = random.choice((bucket_1, bucket_2))
        k = Key(bucket)
        k.key = full_key 
        k.set_contents_from_string(data)
        k.set_acl("public-read")
        return "http://%s/%s" % (bucket.name, full_key)
    else:
        # we dont do random stuff for local storage. Lazy me.
        full_key = "%s/%s.gif" % (key, salt)
        # create folders
        parent = settings.MEDIA_ROOT.joinpath(full_key).parent
        if not parent.exists(): parent.makedirs()
        file(
            settings.MEDIA_ROOT.joinpath(full_key), "wb"
        ).write(data)
        return "/static/%s" % full_key

def update_jpg(key, img, delete_key=None, format="jpeg"):
    import boto
    from boto.s3.key import Key
    s = cStringIO.StringIO()
    img.convert("RGB").save(s, format=format)
    s.seek(0)
    salt = random.randint(0, 1000)
    full_key = "%s/%s.%s" % (key, salt, format)

    if getattr(settings, "USE_S3_BACKEND", False):
        conn = boto.connect_s3(
            settings.S3_ACCESS_KEY, settings.S3_SECRET_KEY
        )
        bucket_1 = conn.create_bucket(settings.S3_BUCKET_1)
        bucket_2 = conn.create_bucket(settings.S3_BUCKET_2)
        # delete old key
        for key_to_delete in [key, delete_key]:
            if not key_to_delete: continue
            for k in itertools.chain(
                bucket_1.get_all_keys(prefix=key_to_delete + "/"), 
                bucket_2.get_all_keys(prefix=key_to_delete + "/"),
            ): k.bucket.delete_key(k.key)
        # create new data
        bucket = random.choice((bucket_1, bucket_2))
        k = Key(bucket)
        k.key = full_key 
        k.set_contents_from_file(s)
        k.set_acl("public-read")
        return "http://%s/%s" % (bucket.name, full_key)
    else:
        # we dont do random stuff for local storage. Lazy me.
        full_key = "%s/%s.%s" % (key, salt, format)
        # create folders
        parent = settings.UPLOAD_DIR.joinpath(full_key).parent
        if not parent.exists(): parent.makedirs()
        file(
            settings.UPLOAD_DIR.joinpath(full_key), "wb"
        ).write(s.read())
        return "/static/uploads/%s" % full_key
# }}}

# get_content_from_path #{{{
def get_content_from_path(p, data=None, number_of_tries=1):
    if (
        p.startswith("http://") or 
        p.startswith("ftp://") or 
        p.startswith("https://")
    ):
        exceptions = []
        for i in range(number_of_tries):
            try:
                if data:
                    return urllib2.urlopen(p, data).read()
                else:
                    return urllib2.urlopen(p).read()
            except Exception, e:
                exceptions.append(e)
        # we are still here, meaning we had exception thrice
        raise exceptions[0]
    if settings.APP_DIR.joinpath(p).exists():
        return file(
            settings.APP_DIR.joinpath(p), 'rb'
        ).read()
    elif settings.APP_DIR.joinpath("../../../").joinpath(p[1:]).exists():
        return file(
            settings.APP_DIR.joinpath("../../../").joinpath(p[1:]), 'rb'
        ).read()
    elif settings.APP_DIR.joinpath(p[1:]).exists():
        return file(
            settings.APP_DIR.joinpath(p[1:]), 'rb'
        ).read()
    raise IOError
#}}}

# send_html_mail # {{{
from email.MIMEMultipart import MIMEMultipart
from email.MIMEText import MIMEText
from smtplib import SMTP, SMTP_SSL
import email.Charset

charset='utf-8'

email.Charset.add_charset(charset, email.Charset.SHORTEST, None, None)

# send_html_mail = messenger.send_html_mail
def send_html_mail_nt(
    subject, sender=settings.DEFAULT_FROM_EMAIL, recip="", context=None, 
    html_template="", text_template="", sender_name="",
    html_content="", text_content="", recip_list=None, sender_formatted=""
):
    from stripogram import html2text
    from feedparser import _sanitizeHTML

    if not context: context = {}
    if html_template:
        html = render(context, html_template)
    else: html = html_content
    if text_template:
        text = render(context, text_template)
    else: text = text_content
    if not text:
        text = html2text(_sanitizeHTML(html,charset))        

    if not recip_list: recip_list = []
    if recip: recip_list.append(recip)

    try:
        if getattr(settings, "EMAIL_USE_SSL", False):
            server = SMTP_SSL(settings.EMAIL_HOST, settings.EMAIL_PORT)
        else:
            server = SMTP(settings.EMAIL_HOST, settings.EMAIL_PORT)
        if settings.EMAIL_USE_TLS:
            server.ehlo()
            server.starttls()
            server.ehlo()
        if settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD:
            server.login(
                settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD
            )
    except Exception, e: 
        print e
        return

    if not sender_formatted:
        sender_formatted = "%s <%s>" % (sender_name, sender) 


    for recip in recip_list:
        msgRoot = MIMEMultipart('related')
        msgRoot['Subject'] = subject.encode("utf8", 'xmlcharrefreplace')
        msgRoot['From'] = sender_formatted.encode(
            "utf8", 'xmlcharrefreplace'
        )
        msgRoot['To'] = recip.encode("utf8", 'xmlcharrefreplace')
        msgRoot.preamble = 'This is a multi-part message in MIME format.'

        msgAlternative = MIMEMultipart('alternative')
        msgRoot.attach(msgAlternative)

        msgAlternative.attach(MIMEText(smart_str(text), _charset=charset))
        msgAlternative.attach(
            MIMEText(smart_str(html), 'html', _charset=charset)
        )

        try:
            server.sendmail(sender, recip, msgRoot.as_string())
        except Exception, e: print e

    server.quit()

send_html_mail = threaded_task(send_html_mail_nt)

def render(context, template):
    from django.template import loader, Context
    if template:
        t = loader.get_template(template)
        return t.render(Context(context))
    return context
# }}}

# send_mail # {{{
def send_mail(
    subject, message, from_email, recipient_list, fail_silently=False,
    auth_user=None, auth_password=None, connection=None
):
    send_html_mail(
        subject, sender=from_email, html_content="<pre>%s</pre>" % message,
        recip_list=recipient_list
    )
# }}}

if getattr(settings, "DUTILS_MONKEY_PATCH_SEND_MAIL", False):
    # you would want to do this if you want all mails to be threaded
    mail.send_mail = send_mail
    # TODO: monkey patch other functions too

# IndianMobileField # {{{
class IndianMobileField(forms.CharField):
    def clean(self, value):
        value = super(IndianMobileField,self).clean(value)
        pattern = "^[0-9\s]+$"
        match = re.match(pattern, value)
        if not match:
            raise forms.ValidationError("Numaric input expected.")
        if len(value) != 10:
            raise forms.ValidationError("Incomplete number found.")
        if value[0] != "9":
            raise forms.ValidationError("Invalid mobile number.")
        return value.strip()
# }}}

# facebook related helpers # {{{ 
# fb_ensure_session_valid # {{{
def fb_ensure_session_valid(request):
    signature_hash = fb_get_signature(request.COOKIES, True)
    assert signature_hash == request.COOKIES[settings.FB_API_KEY]
# }}}

# fb_get_signature # {{{
def fb_get_signature(values_dict, is_cookie_check=False):
    signature_keys = []
    for key in sorted(values_dict.keys()):
        if (is_cookie_check and key.startswith(settings.FB_API_KEY + '_')):
            signature_keys.append(key)
        elif (is_cookie_check is False):
            signature_keys.append(key)

    if (is_cookie_check):
        signature_string = ''.join(
            [
                '%s=%s' % (
                    x.replace(settings.FB_API_KEY + '_',''), values_dict[x]
                )
                for x in signature_keys
            ]
        )
    else:
        signature_string = ''.join(
            ['%s=%s' % (x, values_dict[x]) for x in signature_keys]
        )
    signature_string = signature_string + settings.FB_API_SECRET

    return md5(signature_string).hexdigest()
# }}} 

# fb_get_user_info # {{{
def fb_get_user_info(request, *args):
    get_user_info_data = {
        'method':'Users.getInfo',
        'api_key': settings.FB_API_KEY,
        'session_key': request.COOKIES[settings.FB_API_KEY + '_session_key'],
        'call_id': time.time(),
        'v': '1.0',
        'uids': request.COOKIES[settings.FB_API_KEY + '_user'],
        'fields': ",".join(args),
        'format': 'json',
    }
    get_user_info_hash = fb_get_signature(get_user_info_data)
    get_user_info_data["sig"] = get_user_info_hash
    get_user_info_params = urllib.urlencode(get_user_info_data)
    get_user_info_response = urllib2.urlopen(
        settings.FB_REST_SERVER, get_user_info_params
    ).read()
    return simplejson.loads(get_user_info_response)
# }}} 

# fb_get_uid # {{{
def fb_get_uid(request):
    return request.COOKIES[settings.FB_API_KEY + '_user']
# }}}
# }}} 

# JSONResponse # {{{
class JSONResponse(HttpResponse):
    def __init__(self, data):
        HttpResponse.__init__(
            self, content=simplejson.dumps(data, cls=JSONEncoder),
            #mimetype="text/html",
        ) 
# }}}

# batch_gen # {{{
def batch_gen1(seq, batch_size):
    """ 
    Usage:

    >>> batch_gen1(range(10), 3)
    ((0, 1, 2), (3, 4, 5), (6, 7, 8), (9,))
    to be used when length of seq is known.
    makes one slice call per batch, in case of django db api this is faster
    """

    if isinstance(seq, QuerySet): #4739, not everything django is pragmatic
        length = seq.count()
    else:
        length = len(seq)
    for i in range(0, length, batch_size):
        yield seq[i:i+batch_size]

def batch_gen2(seq, batch_size):
    """ to be used when length is not known """
    it = iter(seq)
    while True:
        values = ()
        for n in xrange(batch_size):
            values += (it.next(),)
    yield values
# }}}

# cacheable # {{{ 
def cacheable(cache_key, timeout=3600):
    """ Usage:

    class SomeClass(models.Model):
        # fields [id, name etc]

        @cacheable("SomeClass_get_some_result_%(id)s")
        def get_some_result(self):
            # do some heavy calculations
            return heavy_calculations()

        @cacheable("SomeClass_get_something_else_%(name)s")
        def get_something_else(self):
            return something_else_calculator(self)
    """
    from django.core.cache import cache
    def paramed_decorator(func):
        def decorated(self):
            key = cache_key % self.__dict__
            if cache.has_key(key):
                return cache[key]
            res = func(self)
            cache.set(key, res, timeout)
            return res
        decorated.__doc__ = func.__doc__
        decorated.__dict__ = func.__dict__
        return decorated 
    return paramed_decorator
# }}} 

# stales_cache # {{{ 
def stales_cache(cache_key):
    """ Usage:

    class SomeClass(models.Model):
        # fields
        name = CharField(...)

        @stales_cache("SomeClass_some_key_that_depends_on_name_%(name)")
        @stales_cache("SomeClass_some_other_key_that_depends_on_name_%(name)")
        def update_name(self, new_name):
            self.name = new_name
            self.save()
    """
    from django.core.cache import cache
    def paramed_decorator(func):
        def decorated(self, *args, **kw):
            key = cache_key % self.__dict__
            cache.delete(key)
            return func(self, *args, **kw)
        decorated.__doc__ = func.__doc__
        decorated.__dict__ = func.__dict__
        return decorated
    return paramed_decorator
# }}} 

# ajax_validator  # {{{
def ajax_validator(request, form_cls):
    """
    Usage
    -----

    # in urls.py have something like this:
    urlpatterns = patterns('',
        # ... other patterns
        (
            r'^ajax/validate-registration-form/$', 'ajax_validator',
            { 'form_cls': 'myproject.accounts.forms.RegistrationForm' }
        ),
    )

    # sample javascript code to use the validator
    $(function(){
        $("#id_username, #id_password, #id_password2, #id_email").blur(function(){
            var url = "/ajax/validate-registration-form/?field=" + this.name;
            var field = this.name;
            $.ajax({
                url: url, data: $("#registration_form").serialize(),
                type: "post", dataType: "json",    
                success: function (response){ 
                    if(response.valid)
                    {
                        $("#"+field+"_errors").html("Sounds good");
                    }
                    else
                    {
                        $("#"+field+"_errors").html(response.errors);
                    }
                }
            });
        });
    });
    """
    mod_name, form_name = get_mod_func(form_cls)
    form_cls = getattr(__import__(mod_name, {}, {}, ['']), form_name)
    form = form_cls(request.POST)
    if "field" in request.GET:
        errors = form.errors.get(request.GET["field"])
        if errors: errors = errors.as_text()
    else:
        errors = form.errors
    return JSONResponse({ "errors": errors, "valid": not errors })
# }}}

# SizeAndTimeMiddleware # {{{ 
class SizeAndTimeMiddleware(object):
    """
    Usage:

    Used for showing size of the page in human readable format and time
    taken to generate the page on the server. To use it, in your base
    template, somewhere put the line:

    <!-- ____SIZE_AND_DATE_PLACEHOLDER____ -->

    May be used on production.
    """
    def process_request(self, request):
        request._request_start_time = time.time() 

    def process_response(self, request, response):
        if not hasattr(request, "_request_start_time"): return response
        if response['Content-Type'].split(';')[0] in (
            'text/html', 'application/xhtml+xml'
        ):
            response.content = smart_unicode(response.content).replace(
                "<!-- ____SIZE_AND_DATE_PLACEHOLDER____ -->", 
                "(%s, %0.3f seconds)" % (
                    filesizeformat(len(response.content)),
                    time.time() - request._request_start_time,
                )
            )
        return response
# }}} 

# JSONEncoder # {{{ 
class JSONEncoder(simplejson.JSONEncoder):
    def default(self, o):
        if isinstance(o, Promise):
            return force_unicode(o)
        if isinstance(o, datetime):
            return o.strftime('%Y-%m-%dT%H:%M:%S')
        if isinstance(o, date):
            return o.strftime('%Y-%m-%d')
        else:
            return super(JSONEncoder, self).default(o)
# }}} 

# try_del # {{{ 
def try_del(d, *args):
    for f in args:
        try:
            del d[f]
        except KeyError: pass
    return d
# }}} 

# get_form_representation # {{{
def get_form_representation(form):
    d = {}
    for field in form.fields:
        value = form.fields[field]
        dd = {}
        if value.label:
            dd["label"] = value.label.title()
        dd["help_text"] = value.help_text
        dd["required"] = value.required
        if field in form.initial:
            dd["initial"] = form.initial[field]
        if value.initial: dd["initial"] = value.initial
        d[field] = dd
    return d
# }}}

# form_handler # {{{
def form_handler(
    request, form_cls, require_login=False, block_get=False, ajax=False,
    next=None, template=None, login_url=None, pass_request=True,
    validate_only=False, **kwargs
):
    """
    Some ajax heavy apps require a lot of views that are merely a wrapper
    around the form. This generic view can be used for them.
    """
    if "next" in request.REQUEST: next = request.REQUEST["next"]
    from django.shortcuts import render_to_response
    is_ajax = request.is_ajax() or ajax or request.REQUEST.get("json")=="true"
    if isinstance(form_cls, basestring):
        # can take form_cls of the form: "project.app.forms.FormName"
        mod_name, form_name = get_mod_func(form_cls)
        form_cls = getattr(__import__(mod_name, {}, {}, ['']), form_name)
    elif isinstance(form_cls, dict):
        for k, v in form_cls.items():
            if not isinstance(v, basestring): continue
            mod_name, form_name = get_mod_func(v)
            form_cls[k] = getattr(__import__(mod_name, {}, {}, ['']), form_name)
    validate_only = (
        validate_only or request.REQUEST.get("validate_only") == "true"
    )
    if login_url is None:
        login_url = getattr(settings, "LOGIN_URL", "/login/")
    if callable(require_login): 
        require_login = require_login(request)
    elif require_login:
        require_login = not request.user.is_authenticated()
    if require_login:
        if require_login == "404":
            raise Http404("login required")
        redirect_url = "%s?next=%s" % (login_url, request.path) # FIXME
        if is_ajax:
            return JSONResponse({ 'success': False, 'redirect': redirect_url })
        return HttpResponseRedirect(redirect_url)
    if block_get and request.method != "POST":
        raise Http404("only post allowed")
    if next: assert template, "template required when next provided"
    def get_form(with_data=False):
        # TODO: allow defaults from URL?
        if isinstance(form_cls, dict):
            assert "fh_form" in request.REQUEST
            form = form_cls[request.REQUEST["fh_form"]]
            forms = form_cls
            for k, f in forms.items():
                forms[k] = f(request) if pass_request else f()
        else:
            form = form_cls(request) if pass_request else form_cls()
            forms = { "form": form }
        if with_data:
            form.data = request.REQUEST
            form.files = request.FILES
            form.is_bound = True
        for f in forms.values():
            if hasattr(f, "init"):
                res = f.init(**kwargs)
                if res: return res
        return form, forms
    if is_ajax and request.method == "GET":
        return JSONResponse(get_form_representation(get_form()[0]))
    if template and request.method == "GET":
        return render_to_response(
            template, get_form()[1],
            context_instance=RequestContext(request)
        )
    form, forms = get_form(with_data=True)
    if form.is_valid():
        if validate_only:
            return JSONResponse({"valid": True, "errors": {}})
        r = form.save()
        if isinstance(r, HttpResponse): return r
        if is_ajax: return JSONResponse(
            {
                'success': True,
                'response': (
                    form.get_json(r) if hasattr(form, "get_json") else r
                )
            }
        )
        if next: return HttpResponseRedirect(next)
        if template: return HttpResponseRedirect(r)
        return JSONResponse(
            {
                'success': True,
                'response': (
                    form.get_json(r) if hasattr(form, "get_json") else r
                )
            }
        )
    if validate_only:
        if "field" in request.REQUEST:
            errors = form.errors.get(request.REQUEST["field"], "")
            if errors: errors = "".join(errors)
        else:
            errors = form.errors
        return JSONResponse({ "errors": errors, "valid": not errors})
    if is_ajax:
        return JSONResponse({ 'success': False, 'errors': form.errors })
    if template:
        return render_to_response(
            template, forms, context_instance=RequestContext(request)
        )
    return JSONResponse({ 'success': False, 'errors': form.errors })
# }}}

# fhurl # {{{ 
def fhurl(reg, form_cls, decorator=lambda x: x, **kw):
    name = kw.pop("name", None)
    kw["form_cls"] = form_cls
    return url(reg, decorator(form_handler), kw, name=name)
# }}} 

# copy_file_to_s3 # {{{ 
s3_operation_lock = threading.Condition(threading.Lock())
def copy_file_to_s3(p, key, bucket):
    from boto.s3.key import Key
    final_url = "http://%s/%s" % (bucket.name, key)

    k = Key(bucket)
    k.key = key 
    k.set_contents_from_string(get_content_from_path(p))
    k.set_acl("public-read")

    return final_url
# }}} 

# cleaned_data # {{{
def clean_data(func):
    def decorated(self, *args, **kw):
        d = self.cleaned_data.get
        return func(self, d(func.__name__[6:]), d, *args, **kw)
    decorated.__doc__ = func.__doc__
    decorated.__dict__ = func.__dict__
    decorated.__name__ = func.__name__
    return decorated
# }}}

# get address book from google # {{{
class GContacts(object):
    def __init__(self, email, password):
        import gdata.contacts
        self.gd_client = gdata.contacts.service.ContactsService()
        self.gd_client.email = email
        self.gd_client.password = password
        self.gd_client.source = 'Your Application Name'
        self.gd_client.ProgrammaticLogin()

    def ListAllContacts(self):
        """Retrieves a list of contacts and displays name and primary email."""
        feed = self.gd_client.GetContactsFeed()
        contacts = []

        while feed:
            for f in feed.entry:
                for e in f.email:
                    if f.title.text:
                        contacts.append({ f.title.text:e.address })
                    else:
                        contacts.append({ e.address:e.address })
            next = feed.GetNextLink()
            feed = None
            if next:
                feed = self.gd_client.GetContactsFeed(next.href)
        return contacts

def get_google_contacts(request):
    import gdata.contacts.service
    gservice = GContacts(
        email = request.GET['email'], 
        password = request.GET['password']
    )
    next = request.GET["next"]
    try:
        contacts = gservice.ListAllContacts()
        request.session["contact_feed"] = contacts
        return HttpResponseRedirect(next)
    except gdata.service.BadAuthentication:
        return HttpResponse('Authentication Error, Login Password mismatch')
#}}}

# get address book from yahoo # {{{
def get_yahoo_contacts(request):
    import time
    import hashlib
    import urllib
    from xml.etree.ElementTree import ElementTree
    import xml.etree.ElementTree
    import urllib2
    from django.utils import simplejson

    appid = settings.YAHOO_APPID
    secret = settings.YAHOO_SECRET_KEY
    if request.GET.get('appid'):
        token = request.GET['token']
        ts = int(time.time())
        sig = hashlib.md5("/WSLogin/V1/wspwtoken_login?appid=%s&token=%s&ts=%s%s" % (appid, token, ts, secret)).hexdigest()
        url = "https://api.login.yahoo.com/WSLogin/V1/wspwtoken_login?appid=%s&token=%s&ts=%s&sig=%s" % (appid, token, ts, sig)
        u = urllib.urlopen(url)
        data = u.read()
        data = data.replace(':',"_")
        tree = ElementTree()
        b = xml.etree.ElementTree.fromstring(data)
        cookie = b.getchildren()[0].find('Cookie').text
        wssid = b.getchildren()[0].find('WSSID').text
        headers = {'Cookie': cookie.strip()}
        url = "http://address.yahooapis.com/v1/searchContacts?format=json&WSSID=%s&appid=%s&token=%s" % (wssid, appid, token)
        req = urllib2.Request(url, headers=headers)
        response = urllib2.urlopen(req)
        addressbook = []
        data = simplejson.loads(response.read())
        for contact in data['contacts']:
            email, name = '',''
            for cf in contact['fields']:
                if cf['type'] == 'email':
                    email = cf.get('data','')
                if cf['type'] == 'name':
                    name = "%s %s" % (cf.get('first',''), cf.get('last',''))
                if cf['type'] == 'yahooid':
                    email = cf.get('data','') + "@yahoo.com"
            if email or name:
                addressbook.append({name:email})
        return HttpResponse("OK GETTING APID")

    appdata = "foobar"
    ts = int(time.time())
    sig = hashlib.md5("/WSLogin/V1/wslogin?appid=%s&appdata=%s&ts=%s%s" % (appid, appdata, ts, secret)).hexdigest()
    url = "https://api.login.yahoo.com/WSLogin/V1/wslogin?appid=%s&appdata=%s&ts=%s&sig=%s" % (appid, appdata, ts, sig)
    return HttpResponseRedirect(url)

#}}}

"""
template helpers
----------------
data profiles: eg registration with error
    stored in db as json
all templates in templates folder
case: data profile to template mapping
"""

# attrdict # {{{ 
class attrdict(dict):
    def __init__(self, *args, **kw):
        dict.__init__(self, *args, **kw)
        self.__dict__ = self
# }}} 

# get_url_with_params # {{{
def get_url_with_params(request, path_override=None, without=None, **extra):
    if path_override: path = path_override
    else: path = request.path
    querystring = request.META.get("QUERY_STRING")
    if not querystring: querystring = ""
    query_dict = dict(cgi.parse_qsl(querystring))
    dict.update(extra)
    if without and without in query_dict: # TODO: handle both string or list
        del query_dict[without]
    querystring = urllib.urlencode(query_dict)
    if querystring:
        return "%s?%s&" % ( path, querystring )
    else:
        return "%s?" % path
#}}}

# mail_exception # {{{
def mail_exception(tag="django"):
    from django.core import mail
    mail.mail_admins(
        "exception in %s" % tag, formatExceptionInfo(12), 
    )
# }}}

# templated decorator # {{{
def templated(template, mimetype="text/html"):
    """
    templated decorator
    ===================

    typical usage:

    @templated("my-template.html")
    def my_view(request, param):
        return { "param": param }

    the view should return a dictionary, or nothing, and templated decorator
    will convert it to django context, load the template, pass the request
    context, and return HttpResponse.

    view can also returh a HttpResponse subclass and templated will let it pass
    through without any processing.
    """
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            res = view(request, *args, **kwargs)
            if res is None:
                res = {}
            elif isinstance(res, HttpResponse):
                return res
            return direct_to_template(request, template, res, mimetype)
        return wrapped
    return decorator
# }}}

# assert_or_404 # {{{ 
def assert_or_404(condition, message="assertion failed"):
    if not condition:
        raise Http404(message)
# }}} 

# debug_call # {{{ 
def debug_call(func):
    if not settings.DEBUG: return func
    def wrapper(*args, **kw):
        logger.debug("%s called with %s, %s" % (func.__name__, args, kw))
        start = time.time()
        ret = func(*args, **kw)
        logger.debug(
            "%s returned %s in %s secs" % (
                func.__name__, ret, time.time() - start
            )
        )
        return ret
    return wrapper
# }}} 

# QuerySetManager # {{{
class QuerySetManager(models.Manager):
    def get_query_set(self):
        return self.model.QuerySet(self.model)

    def __getattr__(self, attr, *args):
        return getattr(self.get_query_set(), attr, *args)
# }}}

# get_fb_access_token_from_request # {{{
def get_fb_access_token_from_request(request, redirect_uri):
    args = dict(client_id=settings.FB_API_KEY, redirect_uri=redirect_uri)
    assert "code" in request.REQUEST
    args["client_secret"] = settings.FB_API_SECRET
    args["code"] = request.REQUEST["code"]
    response = urllib.urlopen(
        "https://graph.facebook.com/oauth/access_token?" +
        urllib.urlencode(args)).read()
    response_data = cgi.parse_qs(response)
    access_token = response_data["access_token"][-1]
    return access_token
# }}}

# JSResponse # {{{ 
class JSResponse(HttpResponse):
    def __init__(self, script):
        HttpResponse.__init__(
            self, content="""
<html>
    <head>
        <script type="text/javascript">%s</script>
    </head>
</html>
            """ % script, mimetype="text/html",
        ) 
# }}} 

# log_user_in # {{{ 
def log_user_in(user, request):
    request.session['_auth_user_backend'] = (
        'django.contrib.auth.backends.ModelBackend'
    )
    request.session['_auth_user_id'] = user.id
# }}} 

# object_list # {{{ 
def object_list(request, queryset, paginate_by=None, page=None,
    allow_empty=True, template_name=None, template_loader=loader,
    extra_context=None, context_processors=None, template_object_name='object',
    mimetype=None, renderer=None
):
    """
    Generic list of objects.

    Templates: ``<app_label>/<model_name>_list.html``
    Context:
        object_list
            list of objects
        is_paginated
            are the results paginated?
        results_per_page
            number of objects per page (if paginated)
        has_next
            is there a next page?
        has_previous
            is there a prev page?
        page
            the current page
        next
            the next page
        previous
            the previous page
        pages
            number of pages, total
        hits
            number of objects, total
        last_on_page
            the result number of the last of object in the
            object_list (1-indexed)
        first_on_page
            the result number of the first object in the
            object_list (1-indexed)
        page_range:
            A list of the page numbers (1-indexed).
        renderer: 
            A callable that will be used to render the data instead of django.
    """
    if extra_context is None: extra_context = {}
    queryset = queryset._clone()
    if paginate_by:
        paginator = Paginator(queryset, paginate_by, allow_empty_first_page=allow_empty)
        if not page:
            page = request.GET.get('page', 1)
        try:
            page_number = int(page)
        except ValueError:
            if page == 'last':
                page_number = paginator.num_pages
            else:
                # Page is not 'last', nor can it be converted to an int.
                raise Http404
        try:
            page_obj = paginator.page(page_number)
        except InvalidPage:
            raise Http404
        c = RequestContext(request, {
            '%s_list' % template_object_name: page_obj.object_list,
            'paginator': paginator,
            'page_obj': page_obj,

            # Legacy template context stuff. New templates should use page_obj
            # to access this instead.
            'is_paginated': page_obj.has_other_pages(),
            'results_per_page': paginator.per_page,
            'has_next': page_obj.has_next(),
            'has_previous': page_obj.has_previous(),
            'page': page_obj.number,
            'next': page_obj.next_page_number(),
            'previous': page_obj.previous_page_number(),
            'first_on_page': page_obj.start_index(),
            'last_on_page': page_obj.end_index(),
            'pages': paginator.num_pages,
            'hits': paginator.count,
            'page_range': paginator.page_range,
        }, context_processors)
    else:
        c = RequestContext(request, {
            '%s_list' % template_object_name: queryset,
            'paginator': None,
            'page_obj': None,
            'is_paginated': False,
        }, context_processors)
        if not allow_empty and len(queryset) == 0:
            raise Http404
    for key, value in extra_context.items():
        if callable(value):
            c[key] = value()
        else:
            c[key] = value
    if not template_name:
        model = queryset.model
        template_name = "%s/%s_list.html" % (
            model._meta.app_label, model._meta.object_name.lower()
        )
    if renderer:
        return HttpResponse(renderer(template_name, c))
    t = template_loader.get_template(template_name)
    return HttpResponse(t.render(c), mimetype=mimetype)
# }}} 

# ip_shell # {{{ 
def ip_shell():
    from IPython import Shell
    Shell.IPShellEmbed()()
# }}} 

# come_back_after_login # {{{
def come_back_after_login(request):
    login_url = getattr(settings, "LOGIN_URL", "/login/")
    return HttpResponseRedirect(login_url + "?next=" + request.path) # FIXME
# }}}

# get_user_profile_class # {{{
def get_user_profile_class():
    if not getattr(settings, 'AUTH_PROFILE_MODULE', False):
        raise SiteProfileNotAvailable(
            'You need to set AUTH_PROFILE_MODULE in your project settings'
        )
    try:
        app_label, model_name = settings.AUTH_PROFILE_MODULE.split('.')
    except ValueError:
        raise SiteProfileNotAvailable(
            'app_label and model_name should be separated by a dot in the '
            'AUTH_PROFILE_MODULE setting.'
        )

    model = models.get_model(app_label, model_name)
    if model is None:
        raise SiteProfileNotAvailable(
            'Unable to load the profile model, check AUTH_PROFILE_MODULE in '
            'your project settings'
        )
    return model
# }}}

# setup_inline_userprofile_admin # {{{
def setup_inline_userprofile_admin(UserProfile=None):
    if not UserProfile: UserProfile = get_user_profile_class()

    class UserProfileInline(admin.StackedInline):
        model = UserProfile

    class UserAdmin(admin.ModelAdmin):
        inlines = [UserProfileInline]

    # Unregister the built in user admin and register the custom 
    # User admin with UserProfile
    admin.site.unregister(User)
    admin.site.register(User, UserAdmin)
# }}}

def dump_json(**kw): return simplejson.dumps(kw, cls=JSONEncoder)

# resize # {{{ 
def resize(img, box, fit=True):
    '''Downsample the image.
    @param img: Image -  an Image-object
    @param box: tuple(x, y) - the bounding box of the result image
    @param fit: boolean - crop the image to fill the box

    Credits: http://united-coders.com/christian-harms/image-resizing-tips-general-and-for-python
    '''
    #preresize image with factor 2, 4, 8 and fast algorithm
    factor = 1
    while img.size[0]/factor > 2*box[0] and img.size[1]*2/factor > 2*box[1]:
        factor *=2
    if factor > 1:
        img.thumbnail((img.size[0]/factor, img.size[1]/factor), Image.NEAREST)

    #calculate the cropping box and get the cropped part
    if fit:
        x1 = y1 = 0
        x2, y2 = img.size
        wRatio = 1.0 * x2/box[0]
        hRatio = 1.0 * y2/box[1]
        if hRatio > wRatio:
            y1 = y2/2-box[1]*wRatio/2
            y2 = y2/2+box[1]*wRatio/2
        else:
            x1 = x2/2-box[0]*hRatio/2
            x2 = x2/2+box[0]*hRatio/2
        img = img.crop((x1,y1,x2,y2))

    #Resize the image with best quality algorithm ANTI-ALIAS
    img.thumbnail(box, Image.ANTIALIAS)

    return img
# }}} 

# NginxSSIMiddleware # {{{
class NginxSSIMiddleware(object):
    '''
    Emulates Nginx SSI module for when a page is rendered from Python. SSI
    include tags are cached for serving directly from Nginx, but if the page is
    being built for the first time, we just serve these directly from Python
    without having to make another request.

    Takes a response object and returns the response with Nginx SSI tags
    resolved.

    Credits: http://joshuajonah.ca/blog/2010/06/18/poor-mans-esi-nginx-ssis-and-django/
    '''
    include_tag = re.compile(
        r'<!--#[\s.]+include[\s.]+virtual=["\'](?P<path>.+)["\'][\s.]+-->'
    )
    def process_response(self, request, response):
        resolver = get_resolver(get_urlconf())
        patterns = resolver._get_url_patterns()
        def get_tag_response(match):
            for pattern in patterns:
                try:
                    view = pattern.resolve(match.group('path')[1:])
                    if view:
                        return view[0](request, *view[1], **view[2]).content
                except Resolver404:
                    pass
            return match.group('path')[1:]
        response.content = re.sub(
            NginxSSIMiddleware.include_tag, get_tag_response, response.content
        )
        return response
# }}}

# dtt_url # {{{
def dtt_url(reg, template, name=None):
    return url(reg, direct_to_template, { 'template': template }, name=name)
# }}}

# make_choices # {{{
def make_choices(s):
    """takes comma separated string, and converts it to choices"""
    choices = []
    for p in s.split(","):
        p = p.strip()
        choices.append((p, p))
    return choices
# }}}

# future_it helper # {{{
def future_it(name, handler, due_on=None, origin="", *args, **kw):
    from dutils.futures.models import Future
    if not due_on: due_on = datetime.now()
    future = Future.objects.schedule(name, handler, due_on, *args, **kw)
    if origin:
        future.log("Created: %s" % origin)
    else:
        future.log("Created")
    return future
# }}}

def global_lock(lock_name):
    def decorated(fun):
        pass

# ContactForm # {{{
class ContactForm(RequestForm):
    name = forms.CharField(max_length=100)
    email = forms.EmailField()
    message = forms.CharField(widget=forms.Textarea)

    def save(self):
        d = self.cleaned_data.get
        mail.mail_managers(
            "new contact message", 
            """
                name:    %(name)s
                email:   %(email)s
                message: %(message)s
            """ % self.cleaned_data,
        )
        return "/contact-us/done/"
# }}}
