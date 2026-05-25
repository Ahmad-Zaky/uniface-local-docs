SET NOCOUNT ON;

-- Sequences
SELECT 'CREATE SEQUENCE [' + SCHEMA_NAME(schema_id) + '].[' + name + '] AS '
       + TYPE_NAME(user_type_id)
       + ' START WITH ' + CAST(start_value AS NVARCHAR(50))
       + ' INCREMENT BY ' + CAST(increment AS NVARCHAR(50))
       + ';' + CHAR(13) + CHAR(10) + 'GO' + CHAR(13) + CHAR(10) AS ddl
FROM sys.sequences
ORDER BY schema_id, name;
GO

-- Tables with columns
SELECT 'CREATE TABLE [' + s.name + '].[' + t.name + '] (' + CHAR(13) + CHAR(10)
       + STUFF((
           SELECT ',' + CHAR(13) + CHAR(10) + '    [' + c.name + '] '
                  + UPPER(tp.name)
                  + CASE
                      WHEN tp.name IN ('varchar','nvarchar','char','nchar')
                        THEN '(' + CASE WHEN c.max_length = -1 THEN 'MAX'
                                        ELSE CAST(CASE WHEN tp.name IN ('nvarchar','nchar')
                                                       THEN c.max_length/2
                                                       ELSE c.max_length END AS NVARCHAR(10)) END + ')'
                      WHEN tp.name IN ('decimal','numeric')
                        THEN '(' + CAST(c.precision AS NVARCHAR(10)) + ',' + CAST(c.scale AS NVARCHAR(10)) + ')'
                      ELSE ''
                    END
                  + CASE WHEN c.is_identity = 1 THEN ' IDENTITY('
                         + CAST(IDENT_SEED(s.name + '.' + t.name) AS NVARCHAR(20)) + ','
                         + CAST(IDENT_INCR(s.name + '.' + t.name) AS NVARCHAR(20)) + ')' ELSE '' END
                  + CASE WHEN c.is_nullable = 0 THEN ' NOT NULL' ELSE ' NULL' END
           FROM sys.columns c
           JOIN sys.types tp ON c.user_type_id = tp.user_type_id
           WHERE c.object_id = t.object_id
           ORDER BY c.column_id
           FOR XML PATH(''), TYPE
         ).value('.', 'NVARCHAR(MAX)'), 1, 3, '')
       + CHAR(13) + CHAR(10) + ');' + CHAR(13) + CHAR(10) + 'GO' + CHAR(13) + CHAR(10) AS ddl
FROM sys.tables t
JOIN sys.schemas s ON t.schema_id = s.schema_id
WHERE t.is_ms_shipped = 0
ORDER BY s.name, t.name;
GO

-- Primary keys and unique constraints
SELECT 'ALTER TABLE [' + s.name + '].[' + t.name + '] ADD CONSTRAINT [' + kc.name + '] '
       + kc.type_desc COLLATE DATABASE_DEFAULT + ' ('
       + STUFF((
           SELECT ', [' + c.name + ']'
           FROM sys.index_columns ic
           JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
           WHERE ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id
           ORDER BY ic.key_ordinal
           FOR XML PATH(''), TYPE
         ).value('.', 'NVARCHAR(MAX)'), 1, 2, '')
       + ');' + CHAR(13) + CHAR(10) + 'GO' + CHAR(13) + CHAR(10) AS ddl
FROM sys.key_constraints kc
JOIN sys.tables t ON kc.parent_object_id = t.object_id
JOIN sys.schemas s ON t.schema_id = s.schema_id
WHERE t.is_ms_shipped = 0
ORDER BY s.name, t.name, kc.name;
GO

-- Non-constraint indexes
SELECT 'CREATE ' + CASE WHEN i.is_unique = 1 THEN 'UNIQUE ' ELSE '' END
       + i.type_desc COLLATE DATABASE_DEFAULT + ' INDEX [' + i.name + '] ON ['
       + s.name + '].[' + t.name + '] ('
       + STUFF((
           SELECT ', [' + c.name + ']' + CASE WHEN ic.is_descending_key = 1 THEN ' DESC' ELSE '' END
           FROM sys.index_columns ic
           JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
           WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.is_included_column = 0
           ORDER BY ic.key_ordinal
           FOR XML PATH(''), TYPE
         ).value('.', 'NVARCHAR(MAX)'), 1, 2, '')
       + ');' + CHAR(13) + CHAR(10) + 'GO' + CHAR(13) + CHAR(10) AS ddl
FROM sys.indexes i
JOIN sys.tables t ON i.object_id = t.object_id
JOIN sys.schemas s ON t.schema_id = s.schema_id
WHERE t.is_ms_shipped = 0
  AND i.is_primary_key = 0
  AND i.is_unique_constraint = 0
  AND i.type > 0
  AND i.name IS NOT NULL
ORDER BY s.name, t.name, i.name;
GO

-- Foreign keys
SELECT 'ALTER TABLE [' + sp.name + '].[' + tp.name + '] ADD CONSTRAINT [' + fk.name
       + '] FOREIGN KEY ('
       + STUFF((
           SELECT ', [' + cp.name + ']'
           FROM sys.foreign_key_columns fkc
           JOIN sys.columns cp ON fkc.parent_object_id = cp.object_id AND fkc.parent_column_id = cp.column_id
           WHERE fkc.constraint_object_id = fk.object_id
           ORDER BY fkc.constraint_column_id
           FOR XML PATH(''), TYPE
         ).value('.', 'NVARCHAR(MAX)'), 1, 2, '')
       + ') REFERENCES [' + sr.name + '].[' + tr.name + '] ('
       + STUFF((
           SELECT ', [' + cr.name + ']'
           FROM sys.foreign_key_columns fkc
           JOIN sys.columns cr ON fkc.referenced_object_id = cr.object_id AND fkc.referenced_column_id = cr.column_id
           WHERE fkc.constraint_object_id = fk.object_id
           ORDER BY fkc.constraint_column_id
           FOR XML PATH(''), TYPE
         ).value('.', 'NVARCHAR(MAX)'), 1, 2, '')
       + ');' + CHAR(13) + CHAR(10) + 'GO' + CHAR(13) + CHAR(10) AS ddl
FROM sys.foreign_keys fk
JOIN sys.tables tp ON fk.parent_object_id = tp.object_id
JOIN sys.schemas sp ON tp.schema_id = sp.schema_id
JOIN sys.tables tr ON fk.referenced_object_id = tr.object_id
JOIN sys.schemas sr ON tr.schema_id = sr.schema_id
ORDER BY sp.name, tp.name, fk.name;
GO

-- Views
SELECT OBJECT_DEFINITION(v.object_id) + CHAR(13) + CHAR(10) + 'GO' + CHAR(13) + CHAR(10) AS ddl
FROM sys.views v
WHERE v.is_ms_shipped = 0
ORDER BY SCHEMA_NAME(v.schema_id), v.name;
GO
