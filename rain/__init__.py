# -*- coding: utf-8 -*-
import schifftszbaern.settings as settings
import png
import urllib
import copy
import settings

from numpy import linalg, asarray, mean
from numpy import array as np_array

from scipy import ndimage
#import matplotlib.pyplot as plt
import numpy as np
import uuid
from datetime import datetime, timedelta
from operator import itemgetter
import math



def build_timestamp(time, forecast=False):
    """
    Takes the given time and subtracts 8 minutes and rounds to the next lower 5minute step.
    """

    #update rate is 5min, so round to the last 5minute step
    off_minutes = time.minute%5
    rounded_delta = timedelta(0,off_minutes*60)

    rounded_time = (time - rounded_delta).replace(second=0, microsecond=0)

    return rounded_time


class Measurement(object):
    """
    Contains rain information for the whole area for a given time

    PNG info:

    1px = approx. 850m

    *******

    radar values:

    < 1mm/h   0/150/255
    < 3mm/h   0/50/255
    < 10mm/h  0/0/200
    < 30mm/h  0/0/125
    < 100mm/h 255/255/0
    > 100mm/h 255/0/0

    Flocken 199/254/254
    schwach 150/255/255
    mässig 100/255/255
    stark 25/255/255
    sehr stark 0/255/255
    extrem 0/200/255


    """

    meteo_values = [{'name':'1mm/h', 'rgb':[0, 150, 255], 'intensity':0}, \
                        {'name':'3mm/h', 'rgb':[0, 50, 255], 'intensity':1}, \
                        {'name':'10mm/h', 'rgb':[0, 0, 200], 'intensity':2}, \
                        {'name':'30mm/h', 'rgb':[0, 0, 125], 'intensity':3},\
                        {'name':'100mm/h', 'rgb':[255, 255, 0], 'intensity':4}, \
                        {'name':'>100mm/h', 'rgb':[255, 0, 0], 'intensity':5}, 
                        {'name':'flakes', 'rgb':[200, 255, 255], 'intensity':10},
                        {'name':'snow weak', 'rgb':[150, 255, 255], 'intensity':11},
                        {'name':'snow moderate', 'rgb':[100, 255, 255], 'intensity':12},
                        {'name':'snow strong', 'rgb':[25, 255, 255], 'intensity':13},
                        {'name':'snow heavy', 'rgb':[0, 255, 255], 'intensity':14},
                        {'name':'snow very heavy', 'rgb':[0, 200, 255], 'intensity':15},
    ]

    @classmethod
    def from_json(cls, position, raster_width, test_field_width, data):
        obj = cls(position, Measurement.timestring_to_timestamp(data['timestamp']), raster_width, test_field_width)
        obj.data = data['data']
        if data.has_key('location'):
            obj.location = data['location']
        else:
            obj.location = obj.rain_at_position(obj.position[0], obj.position[1])
        return obj

    @staticmethod
    def timestring_to_timestamp(timestring):
        return datetime.strptime(str(timestring), settings.DATE_FORMAT)


    def __init__(self, position, timestamp, raster_width, test_field_width, forecast=False, retries=3, url=None):

        self.position = position
        self.raster_width = raster_width #1px is about 850m, raster = 850m*raster_width
        self.test_field_width = test_field_width
        self.forecast = forecast

        self.has_alpha = False
        self.palette = None
        self.data = []
        self.timestamp = timestamp

        timestring = self.get_timestring()

        if not forecast:
            image_name = "PPIMERCATOR.%s.png" % (timestring)
        else:
            #this is sometimes not available from the website, so it is currently not used here
            image_name = "FCSTMERCATOR.%s.png" % (timestring)

        self.image_name = image_name

        if not forecast and not url:
            url = "http://www.srfcdn.ch/meteo/nsradar/media/web/%s" % (self.image_name) 
        
        #use local files. Mainly for testing
        if url.startswith('file:'):
            r = png.Reader(file=open(url.replace('file:', ''), 'r'))
            self.local = True
        else:
            r = png.Reader(file=urllib.urlopen(url))
            self.local = False

        #get the png's properties
        try:
            data = r.read()

            if data[3].has_key('palette'):
                self.palette = data[3]['palette']

            self.width = data[0]
            self.height = data[1]
            self.image_data = list(data[2])
            self.has_alpha = data[3]['alpha']

        except png.FormatError, e:
            print "%s - %s"%(url, e)
            self.width = -1
            self.height = -1            
            self.image_data = -1
            return None

    def __str__(self):
        return self.timestamp

    def __unicode__(self):
        return u"%s"%self.timestamp

    def get_timestring(self):
        return datetime.strftime(self.timestamp, settings.DATE_FORMAT)

    def to_json(self):

        return_dict = {
            'position':self.position,
            'queue':self.data,
            'raster_width':self.raster_width,
            'test_field_width':self.test_field_width,
            'location':location
        }

        return json.dumps(return_dict)

    def analyze_image(self):
        pixel_array = self._read_png(self.position[0]-self.test_field_width/2, self.position[1]-self.test_field_width/2, self.test_field_width, self.test_field_width)
        if len(pixel_array) == 0:
            return None

        image_data = self._make_raster(pixel_array)

        self.data = self._analyze(image_data)
        self.location = self.rain_at_position(self.position[0], self.position[1])

    def _read_png(self, x, y, width, height):
        """
        Returns the png data starting at x, y with width & height
        """

        pixels = []
        count = 0

        for i in range(0, height):
            for j in range(0, width):
                pixels.append(self._get_color_values(x+j, y+i))
                count += 1

        return pixels


    def rain_at_position(self, x, y):
        """
        Get rain intensity for position at x, y
        """

        center = self._get_color_values(x, y)
        pixels = []

        rgb_values = [0,0,0]
        for y_pos in range(y-1, y+2):
            for x_pos in range(x-1, x+2):

                pixel = self._get_color_values(x, y)
                pixels.append(pixel)

                for i in range(0,3):
                    rgb_values[i] += pixel[i]

        max_value = max(pixels, key=tuple)
        return self._get_intensity(np_array(max_value)) or {}


    def _make_raster(self, pixel_array):
        """
        Downsamples the image (pixel_array) so that it is test_field_width/self.raster_width * test_field_width/self.raster_width in size. 
        """

        #Divide image into a raster 
        steps = self.test_field_width/self.raster_width

        #create empty pixel (rgb) array
        raster_array = [[0,0,0] for i in range(steps * steps)]

        #loop through all rasters
        for line in range(0,self.test_field_width):
            multiplicator = int(line/self.raster_width)

            for pixel in range(0,self.test_field_width):
                raster = int(pixel/self.raster_width)

                raster_no = raster+multiplicator*steps

                for i in range(0,3):
                    raster_array[raster_no][i] += pixel_array[line*self.test_field_width+pixel][i] #pixel_array[line][pixel]
            
        #average pixel values
        for pixel in raster_array:
            for j in range(0,3):
                pixel[j] = int(pixel[j]/(self.raster_width*self.raster_width))

        tuple_array = []

        #convert array to tuple
        for pixel in raster_array:
            tuple_array.append(tuple(pixel))

        from PIL import Image

        downsampled_image = Image.new("RGB", (steps, steps,))
        downsampled_image.putdata(tuple_array)

        if settings.SAVE_IMAGES:
            try:
                downsampled_image.save('%s/%s'%(settings.RADAR_IMAGES, self.image_name))
            except:
                pass

        return downsampled_image

    def _analyze(self, data):
        """
        Finds raincells and calculates center of mass & size for each cell.
        Returns an array with the raincells.
        """

        im = np.array(data)

        out = []
        rgb = []

        #make array that only indicates regions (ie raincells), so that for a given x and y 1 = rain and 0 = no rain
        for i in im:
            a = []
            for j in i:
                if j.any():
                    a.append(1)
                else:
                    a.append(0)
            out.append(a)

        regions_data = np.array(out)

        #calculate position & size of the raincells (raincells are simplified (circular shape))
        mask = regions_data
        label_im, nb_labels = ndimage.label(regions_data)
        sizes = ndimage.sum(regions_data, label_im, range(1,nb_labels + 1))
        mean_vals = ndimage.sum(regions_data, label_im, range(1, nb_labels + 1))
        mass = ndimage.center_of_mass(mask,labels=label_im, index=range(1,nb_labels+1))

        for n in range(0,nb_labels):
            rgb.append([0,0,0])

        #calcualte color value for regions
        y = 0
        for line in label_im:
            x = 0
            for j in line:
                if j != 0:
                    for n in range(0,3):
                        rgb[j-1][n] += im[y][x][n]
                x += 1
            y += 1

        result = []

        #calculate average color value for regions and map it to the raincell
        #construct array with all data #FIXME: make obj instead of dict
        for n in range(0,nb_labels):
            region = {}
            region['rgb'] = []
            for m in range(0,3):
                region['rgb'].append(rgb[n][m]/mean_vals[n])

            #FIXME: use own class not dict
            region['intensity'] = self._get_intensity(np_array([round(region['rgb'][0]/5), round(region['rgb'][1]/5), round(region['rgb'][2]/5)]))

            region['size'] = sizes[n]
            region['mean_value'] = mean_vals[n]
            region['center_of_mass'] = [mass[n][0], mass[n][1]]
            region['id'] = uuid.uuid4().hex

            result.append(region)


        #if one wanted a plot
        # plt.figure(figsize=(9,3))

        # plt.subplot(131)
        # plt.imshow(label_im)
        # plt.axis('off')
        # plt.subplot(132)
        # plt.imshow(mask, cmap=plt.cm.gray)
        # plt.axis('off')
        # plt.subplot(133)
        # plt.imshow(label_im, cmap=plt.cm.spectral)
        # plt.axis('off')

        # plt.subplots_adjust(wspace=0.02, hspace=0.02, top=1, bottom=0, left=0, right=1)
        # plt.show()

        return result

    def _get_color_values(self, pixel_x, pixel_y):
        """
        Returns r,g,b for a given pixel. Omits alpha data.
        """

        if self.palette:
            pixel = self.image_data[pixel_y][pixel_x]
            return [self.palette[pixel][0], self.palette[pixel][1], self.palette[pixel][2]]
        else:
            if self.has_alpha:
                factor = 4
            else:
                factor = 3
            if not self.has_alpha or (self.image_data[pixel_y][pixel_x*factor+3] > 0):
                return[self.image_data[pixel_y][pixel_x*factor], self.image_data[pixel_y][pixel_x*factor+1],\
                    self.image_data[pixel_y][pixel_x*factor+2]]
            else:
                return [0,0,0]


    def _get_intensity(self, vector):
        """
        Finds the closest machting intensity on the rain scale.
        FIXME: Doesn't seem to work properly.....
        """

        #vector needs to have some minimal length
        if linalg.norm(vector) < 20:
            return None
        
        #calculate the distance to all intensities & find the minimal distance
        distances = map(lambda value: linalg.norm(vector-np_array((value['rgb'][0],value['rgb'][1],value['rgb'][2]))) ,self.meteo_values)
        min_distance = min(distances)
        
        #just check that the distance is reasonable 
        if int(min_distance) < 200:
            return self.meteo_values[distances.index(min(distances))]
        else:
            return None



class RainPredictor(object):
    """
    Calculates the future movment of rain. Kind of.
    Pretty beta
    """

    def __init__(self, data, last_timestamp, center):

        #sort data by time
        self.data = sorted(data, key=lambda x: x.timestamp, reverse=True)
        self.last_timestamp = last_timestamp
        self.center = center

    def make_forecast(self):

        new_data = []
        n_1_values = []

        #add the cells from the latest samples to an arrary
        for latest_samples in self.data[0].data:
            latest_samples['forecast'] = self.data[0].forecast
            latest_samples['timestamp'] = self.data[0].timestamp
            new_data.append([latest_samples]) #?why list????
            n_1_values.append(latest_samples)


        #go through the rest of the data (time descending)
        for i in range(1, len(self.data)):
            #check if the samples have max. a 10min difference between them.
            try:
                dt = self.data[i-1].timestamp - self.data[i].timestamp

                if(dt.seconds > 10*60):
                    break

            except Exception, e:
                print "error: %s"%e
                continue

            close_points = {}

            #loop through all raincells for a given time and try to find the closest raincell from 5 or 10 minutes ago
            #so we can track the movement of a cell 
            for sample in self.data[i].data:
                position = np_array(sample['center_of_mass'])

                #get distances to all raincells from 5 or 10 minutes ago
                distances = map(lambda new_sample: linalg.norm(position-np_array(new_sample['center_of_mass'])), n_1_values)

                if distances != []:
                    if min(distances) < 4: #just some treshold (about 9.6km (if delta is 5 minutes this is about 115km/h))
                        closest_match = n_1_values[distances.index(min(distances))]
                        if not close_points.has_key(closest_match['id']):
                            close_points[closest_match['id']] = [sample]
                        else:
                            close_points[closest_match['id']].append(sample)
                    else:
                        closest_match = None
                else:
                    closest_match = None

            #find the closest match among the cells for a given time
            for last_sample in n_1_values: #FIXME: rename to new_smample
                position = np_array(last_sample['center_of_mass'])
                if close_points.has_key(last_sample['id']):
                    distances = map(lambda close_sample: linalg.norm(position-np_array(close_sample['center_of_mass'])), close_points[last_sample['id']])
                    closest_match = close_points[last_sample['id']][distances.index(min(distances))]
                    closest_match['movement'] = position - np_array(closest_match['center_of_mass']) #FIXME: add movement to n-1 value
                    closest_match['forecast'] = self.data[i].forecast
                    closest_match['timestamp'] = self.data[i].timestamp
                else:
                    #FIXME: change to last pos
                    closest_match = {'center_of_mass':[-99, -99], 'movement':[0,0], 'size':0}
                    closest_match['forecast'] = self.data[i].forecast
                    closest_match['timestamp'] = self.data[i].timestamp

                for history in new_data:
                    if last_sample in history:
                        history.append(closest_match)


            n_1_values = self.data[i].data

        hits = []

        #Loop through a raincells history (past positions) and calculate the movement for the next 50min
        for history in new_data:

            if settings.DEBUG:
                print "***** cell forecast *****"

            #get average movement
            coms = np_array(map(lambda sample: sample['movement'], history[1:settings.NO_SAMPLES])) #FIXME: movement in wrong sample
            mean = coms.mean(axis=0)

            #get last position
            future_pos = np_array(history[0]['center_of_mass'])
            try:
                radius_abs = math.sqrt(history[0]['size']/math.pi)

            except Exception, e:
                print e
                radius = 0

            if settings.DEBUG:
                print "last_pos: %s, mean %s"%(future_pos, mean)

            last_time = history[0]['timestamp']
            last_diff = -1
            first_sample = True

            #calculate future positions in increments of 5min #FIXME: check if the average of the movement is for 5/10min
            for i in range(1,10):
                future_pos = future_pos + mean 
                forecast_sample = {}
                forecast_sample['forecast'] = True
                forecast_sample['center_of_mass'] = future_pos
                forecast_sample['intensity'] = history[0]['intensity']
                forecast_sample['size'] = history[0]['size']
                time = last_time + timedelta(0,60*5*i)
                forecast_sample['timestamp'] = time
                history.append(forecast_sample)

                #rain cells need to have a certain size
                if forecast_sample['size'] > 3:# or not forecast_sample['forecast']:
                    continue

                radius_abs = math.sqrt(forecast_sample['size']/math.pi)
                diff = linalg.norm((self.center, self.center) - np_array(forecast_sample['center_of_mass']))
                if not first_sample:
                    #check if the cell coming closer
                    if diff > last_diff:
                        forecast_sample['moving_away'] = True

                if settings.DEBUG:
                    print "%s %s - dist: %s - forecast: %s"%(forecast_sample['center_of_mass'], forecast_sample['size'], diff-radius_abs, forecast_sample['forecast'])
                
                #check if the cell will hit the location
                if(diff-radius_abs < 0.5):
                    #hits.append({'history':history, 'dtime':forecast_sample['timestamp']-int(now), 'timestamp':forecast_sample['timestamp']})
                    hits.append({'dtime':(forecast_sample['timestamp']-datetime.now()).total_seconds(), 'timestamp':forecast_sample['timestamp'], 'sample':forecast_sample})
                    print forecast_sample['timestamp']
                    print (forecast_sample['timestamp']-datetime.now()).total_seconds()
                    if settings.DEBUG:
                        print "last: %s, timestamp: %s, now: %s"%(self.last_timestamp, forecast_sample['timestamp']-self.last_timestamp, (self.last_timestamp-datetime.now()).total_seconds())
                        print "direct hit"
                    break

                if first_sample:
                    first_sample = False

                last_diff = diff

        next_hit_intensity = None
        time_to_next_hit = None
        next_impact_time = None
        next_size = -1

        if hits and settings.DEBUG:
            print "****** impacts *******"

        #loop through all cells that'll hit the location and get the one that'll hit the location the soonest
        for hit in hits:
            last_intensity = None
            #for sample in hit['history']:
            sample = hit['sample']
            if sample['forecast'] and sample['size'] != 0:
                last_intensity = sample['intensity']
                if(time_to_next_hit is None or time_to_next_hit > hit['dtime']):
                    if settings.DEBUG:
                        print sample
                    time_to_next_hit = hit['dtime']
                    next_impact_time = hit['timestamp']
                    time_to_next_hit_intensity = last_intensity
                    next_size = sample['size']
                elif time_to_next_hit == hit['dtime'] and sample['intensity'] > time_to_next_hit_intensity:
                    time_to_next_hit = hit['dtime']
                    next_hit_intensity = last_intensity
                    next_impact_time = hit['timestamp']
                    next_size = sample['size']


        if settings.TWEET_PREDICTION:
            try:
                if time_to_next_hit:
                    from schifftszbaern.utils import send_tweet
                    send_tweet("%s, %s, %s"%(str(next_impact_time)[:-2], next_hit_intensity, next_size))
                    pass


            except Exception, e:
                print e
                pass

        return time_to_next_hit, next_size, next_impact_time


