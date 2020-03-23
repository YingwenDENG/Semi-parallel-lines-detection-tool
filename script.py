import math
import os
import time
import arcpy

arcpy.env.overwriteOutput = True

mxd = arcpy.mapping.MapDocument("current")
df = mxd.activeDataFrame  

input_line = arcpy.GetParameterAsText(0)
SearchDistance = arcpy.GetParameterAsText(1) # unit meter
AngleTolerance = float(arcpy.GetParameterAsText(2)) # unit degree
WorkPath = arcpy.GetParameterAsText(3)
OutFeatureClass1 = WorkPath + "\\output1.shp"
OutFeatureClass2 = WorkPath + "\\output2.shp"

MaximumSegmentLength = 5  # meter

TempDir = WorkPath
dbName = "lines_" + time.strftime("%Y-%m-%d_%H%M%S") + ".gdb"

TempDB = TempDir + "\\" + dbName
SegmentsFC1 = TempDB + "\\Segments1"
SegmentsFC2 = TempDB + "\\Segments2" 
NearTable = TempDB + "\\NearTable"
NearDist = TempDB + "\\NearDist"
NearDistAngle = TempDB + "\\NearDistAngle"
Result = TempDB + "\\Result"


def getAngle(startX, startY, endX, endY):
    flip = startY < endY  # flip the vector if the angle would be > 180
    dx = startX - endX if flip else endX - startX
    dy = startY - endY if flip else endY - startY
    angle = math.atan2(dy, dx)
    return (angle * 180) / math.pi  # return angle in degree


def addFeature(startPoint, endPoint, insertCount):
    points = arcpy.Array()
    points.add(startPoint)
    points.add(endPoint)

    # Create a new row buffer and set shape
    feature = insertCursor[insertCount].newRow()
    feature.shape = points

    # Set the angle of the segment
    feature.setValue("Angle", getAngle(startPoint.X, startPoint.Y, endPoint.X, endPoint.Y))

    # insert the new feature and clear the array
    insertCursor[insertCount].insertRow(feature)


def addNonParallelIds(segmentsFC, idColumn, missingColumn):

    searchCursorSegment = arcpy.SearchCursor(segmentsFC, None, None, "OBJECTID")
    insertCursorResult = arcpy.InsertCursor(Result)
    for segment in searchCursorSegment:
        segmentId = segment.getValue("OBJECTID")
        found = False
        searchCursorResult = arcpy.SearchCursor(Result, None, None, idColumn)
        for resultLine in searchCursorResult:
            resultId = resultLine.getValue(idColumn)
            if resultId == segmentId:
                found = True
                break
        if found is False:
            row = insertCursorResult.newRow()
            row.setValue(idColumn, segmentId)
            row.setValue(missingColumn, -1)
            insertCursorResult.insertRow(row)
        del searchCursorResult
    del searchCursorSegment
    del insertCursorResult


# Get some important information about the input
desc = arcpy.Describe(input_line)
spatialReference = desc.spatialReference
shapeName = desc.shapeFieldName

# create temp database and feature class
arcpy.AddMessage("Creating temp database and feature class")
arcpy.CreateFileGDB_management(TempDir, dbName)
arcpy.CreateFeatureclass_management(TempDB, "Segments1", "POLYLINE", None, "DISABLED", "DISABLED", spatialReference)
arcpy.AddField_management(SegmentsFC1, "Angle", "DOUBLE")
arcpy.CreateFeatureclass_management(TempDB, "Segments2", "POLYLINE", None, "DISABLED", "DISABLED", spatialReference)
arcpy.AddField_management(SegmentsFC2, "Angle", "DOUBLE")

# break the input lines into segments
searchCursor = arcpy.SearchCursor(input_line)
insertCursor = [
    arcpy.InsertCursor(SegmentsFC1, spatialReference),
    arcpy.InsertCursor(SegmentsFC2, spatialReference)
]

arcpy.AddMessage("Breaking lines into segments for each polyline")

geomCount = 0
for fromRow in searchCursor:
    geom = fromRow.getValue(shapeName)
    for polyline in geom:
        previousPoint = polyline[0]
        for point in polyline:
            if point is not None and point != previousPoint:
                addFeature(previousPoint, point, geomCount)
                previousPoint = point
    geomCount += 1
del searchCursor
del insertCursor

# Generate near table of features within search distance
arcpy.AddMessage("Generating near table")
arcpy.GenerateNearTable_analysis(SegmentsFC1, SegmentsFC2, NearTable, SearchDistance,
                                 "NO_LOCATION", "NO_ANGLE", "ALL")

# reduce the near table to just the non-touching features
arcpy.TableSelect_analysis(NearTable, NearDist, "NEAR_DIST > 0")

# add fields for from feature angle, to feature angle
arcpy.AddField_management(NearDist, "FromAngle", "DOUBLE")
arcpy.AddField_management(NearDist, "ToAngle", "DOUBLE")
arcpy.AddField_management(NearDist, "AngleDiff", "DOUBLE")

# create a join to copy the angles to the fromAngle and toAngle fields
arcpy.AddMessage("Copying angles")
arcpy.MakeTableView_management(NearDist, "ND")
arcpy.AddJoin_management("ND", "IN_FID", SegmentsFC1, "OBJECTID")
arcpy.CalculateField_management("ND", "NearDist.FromAngle", "!Segments1.Angle!", "PYTHON")
arcpy.RemoveJoin_management("ND")

arcpy.AddJoin_management("ND", "NEAR_FID", SegmentsFC2, "OBJECTID")
arcpy.CalculateField_management("ND", "NearDist.ToAngle", "!Segments2.Angle!", "PYTHON")
arcpy.RemoveJoin_management("ND")

# calculate the difference in angle
arcpy.AddMessage("Resolving differences of angles")
arcpy.CalculateField_management(NearDist, "AngleDiff", "abs(!FromAngle! - !ToAngle!)", "PYTHON")
# flip the AngleDiff if it is an larger angle
arcpy.MakeTableView_management(NearDist, "NDA", "AngleDiff > %s" % str(180 - AngleTolerance))
arcpy.CalculateField_management("NDA", "AngleDiff", "180 - !AngleDiff!", "PYTHON")

# Reduce the near table to similar angles
arcpy.TableSelect_analysis(NearDist, NearDistAngle, "AngleDiff < %s" % str(AngleTolerance))

# create an result table for all segments with their pairID(IN_FID) and set the non-parallel ones' pair into -1
arcpy.TableSelect_analysis(NearDistAngle, Result)
arcpy.DeleteField_management(Result, "FromAngle")
arcpy.DeleteField_management(Result, "ToAngle")
arcpy.DeleteField_management(Result, "AngleDiff")
arcpy.DeleteField_management(Result, "NEAR_DIST")
 

addNonParallelIds(SegmentsFC1, "IN_FID", "NEAR_FID")
addNonParallelIds(SegmentsFC2, "NEAR_FID", "IN_FID")

# join to the table and export to OutFeatureClass
# join to the table and export to OutFeatureClass
arcpy.AddMessage("Exporting records")
arcpy.MakeFeatureLayer_management(SegmentsFC1, "SegFC1")
arcpy.AddJoin_management("SegFC1", "OBJECTID", NearDistAngle, "IN_FID", "KEEP_COMMON")
arcpy.CopyFeatures_management("SegFC1", OutFeatureClass1)
selection1 = arcpy.mapping.Layer(OutFeatureClass1)  
arcpy.mapping.AddLayer(df, selection1, "TOP")  

arcpy.MakeFeatureLayer_management(SegmentsFC2, "SegFC2")
arcpy.AddJoin_management("SegFC2", "OBJECTID", NearDistAngle, "NEAR_FID", "KEEP_COMMON")
arcpy.CopyFeatures_management("SegFC2", OutFeatureClass2)
selection2 = arcpy.mapping.Layer(OutFeatureClass2)  
arcpy.mapping.AddLayer(df, selection2, "TOP")  

