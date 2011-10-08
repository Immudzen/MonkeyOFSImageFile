import ZODB.blob
import OFS.Image
from OFS.Image import Pdata
from OFS.Image import getImageInfo
from ZPublisher.HTTPRequest import FileUpload
from zLOG.EventLogger import log_write
import zLOG
from webdav.common import rfc1123_date
from ZPublisher.Iterators import filestream_iterator
from DateTime.DateTime import DateTime
from ZPublisher import HTTPRangeSupport
from mimetools import choose_boundary
from zope.interface import implementedBy
from ZODB.interfaces import IBlobStorage

def log(name, short="", longMessage="", error_level=zLOG.INFO, reraise=0):
    "Log an error to a file"
    log_write(name, error_level, str(short), str(longMessage), None)

def _range_request_handler(self, REQUEST, RESPONSE):
    # HTTP Range header handling: return True if we've served a range
    # chunk out of our data.
    range = REQUEST.get_header('Range', None)
    request_range = REQUEST.get_header('Request-Range', None)
    if request_range is not None:
        # Netscape 2 through 4 and MSIE 3 implement a draft version
        # Later on, we need to serve a different mime-type as well.
        range = request_range
    if_range = REQUEST.get_header('If-Range', None)
    if range is not None:
        ranges = HTTPRangeSupport.parseRange(range)

        if if_range is not None:
            # Only send ranges if the data isn't modified, otherwise send
            # the whole object. Support both ETags and Last-Modified dates!
            if len(if_range) > 1 and if_range[:2] == 'ts':
                # ETag:
                if if_range != self.http__etag():
                    # Modified, so send a normal response. We delete
                    # the ranges, which causes us to skip to the 200
                    # response.
                    ranges = None
            else:
                # Date
                date = if_range.split( ';')[0]
                try: mod_since=long(DateTime(date).timeTime())
                except: mod_since=None
                if mod_since is not None:
                    if self._p_mtime:
                        last_mod = long(self._p_mtime)
                    else:
                        last_mod = long(0)
                    if last_mod > mod_since:
                        # Modified, so send a normal response. We delete
                        # the ranges, which causes us to skip to the 200
                        # response.
                        ranges = None

        if ranges:
            # Search for satisfiable ranges.
            satisfiable = 0
            for start, end in ranges:
                if start < self.size:
                    satisfiable = 1
                    break

            if not satisfiable:
                RESPONSE.setHeader('Content-Range',
                    'bytes */%d' % self.size)
                RESPONSE.setHeader('Accept-Ranges', 'bytes')
                RESPONSE.setHeader('Last-Modified',
                    rfc1123_date(self._p_mtime))
                RESPONSE.setHeader('Content-Type', self.content_type)
                RESPONSE.setHeader('Content-Length', self.size)
                RESPONSE.setStatus(416)
                return True

            ranges = HTTPRangeSupport.expandRanges(ranges, self.size)

            if len(ranges) == 1:
                # Easy case, set extra header and return partial set.
                start, end = ranges[0]
                size = end - start

                RESPONSE.setHeader('Last-Modified',
                    rfc1123_date(self._p_mtime))
                RESPONSE.setHeader('Content-Type', self.content_type)
                RESPONSE.setHeader('Content-Length', size)
                RESPONSE.setHeader('Accept-Ranges', 'bytes')
                RESPONSE.setHeader('Content-Range',
                    'bytes %d-%d/%d' % (start, end - 1, self.size))
                RESPONSE.setStatus(206) # Partial content

                data = self.data
                if isinstance(data, str):
                    RESPONSE.write(data[start:end])
                    return True
                    
                elif isinstance(data, ZODB.blob.Blob):
                    blob_file = self.data.open('r')
                    blob_file.seek(start, 0)
                    size = end - start
                    chunksize = 1<<16
                    position = start
                    while size > 0:
                        if (end - position) < chunksize:
                            chunksize = (end - position)
                        RESPONSE.write(blob_file.read(chunksize))
                        position += chunksize
                        size -= chunksize
                    return True
                    
                else: #handle pdata case
                    # Linked Pdata objects. Urgh.
                    pos = 0
                    while data is not None:
                        l = len(data.data)
                        pos = pos + l
                        if pos > start:
                            # We are within the range
                            lstart = l - (pos - start)

                            if lstart < 0: lstart = 0

                            # find the endpoint
                            if end <= pos:
                                lend = l - (pos - end)

                                # Send and end transmission
                                RESPONSE.write(data[lstart:lend])
                                break

                            # Not yet at the end, transmit what we have.
                            RESPONSE.write(data[lstart:])

                        data = data.next

                    return True

            else:
                boundary = choose_boundary()

                # Calculate the content length
                size = (8 + len(boundary) + # End marker length
                    len(ranges) * (         # Constant lenght per set
                        49 + len(boundary) + len(self.content_type) +
                        len('%d' % self.size)))
                for start, end in ranges:
                    # Variable length per set
                    size = (size + len('%d%d' % (start, end - 1)) +
                        end - start)


                # Some clients implement an earlier draft of the spec, they
                # will only accept x-byteranges.
                draftprefix = (request_range is not None) and 'x-' or ''

                RESPONSE.setHeader('Content-Length', size)
                RESPONSE.setHeader('Accept-Ranges', 'bytes')
                RESPONSE.setHeader('Last-Modified',
                    rfc1123_date(self._p_mtime))
                RESPONSE.setHeader('Content-Type',
                    'multipart/%sbyteranges; boundary=%s' % (
                        draftprefix, boundary))
                RESPONSE.setStatus(206) # Partial content

                data = self.data
                # The Pdata map allows us to jump into the Pdata chain
                # arbitrarily during out-of-order range searching.
                pdata_map = {}
                pdata_map[0] = data

                for start, end in ranges:
                    RESPONSE.write('\r\n--%s\r\n' % boundary)
                    RESPONSE.write('Content-Type: %s\r\n' %
                        self.content_type)
                    RESPONSE.write(
                        'Content-Range: bytes %d-%d/%d\r\n\r\n' % (
                            start, end - 1, self.size))

                    if isinstance(data, str):
                        RESPONSE.write(data[start:end])

                    elif isinstance(data, ZODB.blob.Blob):
                        blob_file = self.data.open('r')
                        blob_file.seek(start, 0)
                        size = end - start
                        chunksize = 1<<16
                        position = start
                        while size > 0:
                            if (end - position) < chunksize:
                                chunksize = (end - position)
                            RESPONSE.write(blob_file.read(chunksize))
                            position += chunksize
                            size -= chunksize
                        return True
                        
                    else:
                        # Yippee. Linked Pdata objects. The following
                        # calculations allow us to fast-forward through the
                        # Pdata chain without a lot of dereferencing if we
                        # did the work already.
                        first_size = len(pdata_map[0].data)
                        if start < first_size:
                            closest_pos = 0
                        else:
                            closest_pos = (
                                ((start - first_size) >> 16 << 16) +
                                first_size)
                        pos = min(closest_pos, max(pdata_map.keys()))
                        data = pdata_map[pos]

                        while data is not None:
                            l = len(data.data)
                            pos = pos + l
                            if pos > start:
                                # We are within the range
                                lstart = l - (pos - start)

                                if lstart < 0: lstart = 0

                                # find the endpoint
                                if end <= pos:
                                    lend = l - (pos - end)

                                    # Send and loop to next range
                                    RESPONSE.write(data[lstart:lend])
                                    break

                                # Not yet at the end, transmit what we have.
                                RESPONSE.write(data[lstart:])

                            data = data.next
                            # Store a reference to a Pdata chain link so we
                            # don't have to deref during this request again.
                            pdata_map[pos] = data

                # Do not keep the link references around.
                del pdata_map
                    
                RESPONSE.write('\r\n--%s--\r\n' % boundary)
                return True

def index_html(self, REQUEST, RESPONSE):
    """
    The default view of the contents of a File or Image.

    Returns the contents of the file or image.  Also, sets the
    Content-Type HTTP header to the objects content type.
    """

    if self._if_modified_since_request_handler(REQUEST, RESPONSE):
        # we were able to handle this by returning a 304
        # unfortunately, because the HTTP cache manager uses the cache
        # API, and because 304 responses are required to carry the Expires
        # header for HTTP/1.1, we need to call ZCacheable_set here.
        # This is nonsensical for caches other than the HTTP cache manager
        # unfortunately.
        self.ZCacheable_set(None)
        return ''
    
    if self.precondition and hasattr(self, str(self.precondition)):
        # Grab whatever precondition was defined and then
        # execute it.  The precondition will raise an exception
        # if something violates its terms.
        c=getattr(self, str(self.precondition))
        if hasattr(c,'isDocTemp') and c.isDocTemp:
            c(REQUEST['PARENTS'][1],REQUEST)
        else:
            c()
    
    if self._range_request_handler(REQUEST, RESPONSE):
        # we served a chunk of content in response to a range request.
        return ''
    
    RESPONSE.setHeader('Last-Modified', rfc1123_date(self._p_mtime))
    RESPONSE.setHeader('Content-Type', self.content_type)
    RESPONSE.setHeader('Content-Length', self.size)
    RESPONSE.setHeader('Accept-Ranges', 'bytes')

    if self.ZCacheable_isCachingEnabled():
        result = self.ZCacheable_get(default=None)
        if result is not None:
            # We will always get None from RAMCacheManager and HTTP
            # Accelerated Cache Manager but we will get
            # something implementing the IStreamIterator interface
            # from a "FileCacheManager"
            return result
    
    self.ZCacheable_set(None)
    data = self.data
    if isinstance(data, str):
        RESPONSE.setBase(None)
        return data
    
    elif isinstance(data, ZODB.blob.Blob):
        RESPONSE.setBase(None)
        filename = data._p_blob_uncommitted or data.committed()
        return filestream_iterator(filename, 'rb')
    
    else:
        while data is not None:
            RESPONSE.write(data.data)
            data=data.next
            
    return ''

def file_update_data(self, data, content_type=None, size=None):
    if isinstance(data, unicode):
        raise TypeError('Data can only be str or file-like.  '
                        'Unicode objects are expressly forbidden.')

    if content_type is not None: self.content_type=content_type
    if size is None: size=len(data)
    self.size=size
    if data:
        if IBlobStorage.providedBy(self._p_jar.db().storage):
            self.data = ZODB.blob.Blob(data)
        else:
            self.data = data
    else:
        self.data = ''
    self.ZCacheable_invalidate()
    self.ZCacheable_set(None)
    self.http__refreshEtag()

def resave_to_blob(self):
    "put the current data in a blob"
    data = self.data
    if data and not isinstance(data, ZODB.blob.Blob) and IBlobStorage.providedBy(self._p_jar.db().storage):
        self.data = ZODB.blob.Blob(str(data))

def image_update_data(self, data, content_type=None, size=None):
    if isinstance(data, unicode):
        raise TypeError('Data can only be str or file-like.  '
                        'Unicode objects are expressly forbidden.')
    
    if size is None: size=len(data)

    self.size=size
    if data:
        if IBlobStorage.providedBy(self._p_jar.db().storage):
            self.data = ZODB.blob.Blob(data)
        else:
            self.data = data
    else:
        self.data = ''

    ct, width, height = getImageInfo(data)
    if ct:
        content_type = ct
    if width >= 0 and height >= 0:
        self.width = width
        self.height = height

    # Now we should have the correct content type, or still None
    if content_type is not None: self.content_type = content_type

    self.ZCacheable_invalidate()
    self.ZCacheable_set(None)
    self.http__refreshEtag()

def _read_data(self, file):
    import transaction

    n=1 << 16

    # Make sure we have an _p_jar, even if we are a new object, by
    # doing a sub-transaction commit.
    transaction.savepoint(optimistic=True)

    if isinstance(file, str):
        size=len(file)
        if size<n or IBlobStorage.providedBy(self._p_jar.db().storage):
            #for blobs we don't have to cut anything up or if the size<n
            return file,size
        # Big string: cut it into smaller chunks
        file = StringIO(file)

    if isinstance(file, FileUpload) and not file:
        raise ValueError, 'File not specified'

    if hasattr(file, '__class__') and file.__class__ is Pdata:
        size=len(file)
        return file, size

    seek=file.seek
    read=file.read

    seek(0,2)
    size=end=file.tell()

    if IBlobStorage.providedBy(self._p_jar.db().storage):
        seek(0)
        return read(size), size

    if size <= 2*n:
        seek(0)
        if size < n: return read(size), size
        return Pdata(read(size)), size



    if self._p_jar is None:
        # Ugh
        seek(0)
        return Pdata(read(size)), size

    # Now we're going to build a linked list from back
    # to front to minimize the number of database updates
    # and to allow us to get things out of memory as soon as
    # possible.
    next = None
    while end > 0:
        pos = end-n
        if pos < n:
            pos = 0 # we always want at least n bytes
        seek(pos)

        # Create the object and assign it a next pointer
        # in the same transaction, so that there is only
        # a single database update for it.
        data = Pdata(read(end-pos))
        self._p_jar.add(data)
        data.next = next

        # Save the object so that we can release its memory.
        transaction.savepoint(optimistic=True)
        data._p_deactivate()
        # The object should be assigned an oid and be a ghost.
        assert data._p_oid is not None
        assert data._p_state == -1

        next = data
        end = pos

    return next, size

def file__str__(self): 
    return self.data.open('r').read() if isinstance(self.data, ZODB.blob.Blob) else str(self.data)

def file_PrincipiaSearchSource(self):
    """ Allow file objects to be searched.
    """
    if self.content_type.startswith('text/'):
        return self.data.open('r').read() if isinstance(self.data, ZODB.blob.Blob) else str(self.data)
    return ''

OFS.Image.File._range_request_handler = _range_request_handler
OFS.Image.File.index_html =  index_html
OFS.Image.File.update_data = file_update_data
OFS.Image.File.resave_to_blob = resave_to_blob
OFS.Image.File._read_data = _read_data
OFS.Image.File.__str__ = file__str__
OFS.Image.File.PrincipiaSearchSource = file_PrincipiaSearchSource
OFS.Image.Image.update_data = image_update_data
