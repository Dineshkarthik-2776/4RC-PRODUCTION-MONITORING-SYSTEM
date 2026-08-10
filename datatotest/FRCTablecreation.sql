USE [Dashboard]
GO

/****** Object:  Table [dbo].[FRC]    Script Date: 4/7/2026 17:55:20 ******/
SET ANSI_NULLS ON
GO

SET QUOTED_IDENTIFIER ON
GO

CREATE TABLE [dbo].[FRC](
	[id] [bigint] IDENTITY(1,1) NOT NULL,
	[MachineID] [varchar](50) NULL,
	[RecipeCode] [varchar](50) NULL,
	[LineSpeedActual] [float] NULL,
	[LineSpeedSet] [float] NULL,
	[TimeStamp] [datetime] NULL,
	[DateCreated] [datetime] NULL,
	[ProcessedFlag] [bit] NULL,
 CONSTRAINT [PK_FRC] PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
GO

ALTER TABLE [dbo].[FRC] ADD  CONSTRAINT [DF_FRC_ProcessedFlag]  DEFAULT ((0)) FOR [ProcessedFlag]
GO


