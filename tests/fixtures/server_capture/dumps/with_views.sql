-- MariaDB dump 10.19  Distrib 10.11.6-MariaDB, for debian-linux-gnu (x86_64)
/*!40101 SET NAMES utf8mb4 */;

DROP TABLE IF EXISTS `wp_options`;
CREATE TABLE `wp_options` (
  `option_id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  PRIMARY KEY (`option_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
INSERT INTO `wp_options` VALUES (1),(2);

DROP TABLE IF EXISTS `wp_posts`;
CREATE TABLE `wp_posts` (
  `ID` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  PRIMARY KEY (`ID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

--
-- Temporary table structure for view `wp_recent_posts`
--
DROP TABLE IF EXISTS `wp_recent_posts`;
/*!50001 DROP VIEW IF EXISTS `wp_recent_posts`*/;
SET @saved_cs_client     = @@character_set_client;
/*!50001 CREATE VIEW `wp_recent_posts` AS SELECT
 1 AS `ID` */;
SET character_set_client = @saved_cs_client;

--
-- Final view structure for view `wp_recent_posts`
--
/*!50001 DROP VIEW IF EXISTS `wp_recent_posts`*/;
/*!50001 SET @saved_cs_client          = @@character_set_client */;
/*!50001 CREATE ALGORITHM=UNDEFINED */
/*!50013 DEFINER=`amarsonar`@`%` SQL SECURITY DEFINER */
/*!50001 VIEW `wp_recent_posts` AS select `wp_posts`.`ID` AS `ID` from `wp_posts` */;
-- Dump completed
